"""Provider-agnostic LLM client: one call signature for every model.

    llm = LLM("gemini/gemini-3.6-flash", fallbacks=["gemini/gemini-3.1-flash-lite"])
    reply = llm.json(system, user, schema=MyPydanticModel, tag="mine:E01-E05")

Swap models by changing the "provider/model" string, nothing else:
    gemini, groq, openrouter, openai, ollama, custom (any OpenAI-compatible URL, e.g. Kuku's
    own gateway via CLIPHANGER_BASE_URL + CLIPHANGER_API_KEY) and anthropic (native SDK).

Reliability on free tiers:
  * every successful reply is cached on disk, keyed by (model, prompt): re-runs are free,
    reproducible, and resume where a quota error stopped them;
  * 429/5xx/timeouts are retried with backoff, then the next model in the chain is tried;
  * replies are parsed as JSON and validated against a schema, with one repair round.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

PROVIDERS: dict[str, dict[str, Any]] = {
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key_env": "GEMINI_API_KEY", "reasoning": True},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "openai": {"base_url": None, "key_env": "OPENAI_API_KEY", "reasoning": True},
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None},
    "custom": {"base_url_env": "CLIPHANGER_BASE_URL", "key_env": "CLIPHANGER_API_KEY"},
    "anthropic": {"key_env": "ANTHROPIC_API_KEY"},
}
RETRYABLE = {408, 409, 429, 500, 502, 503, 504}

_RATE_LOCK = threading.Lock()
_RECENT_CALLS: dict[str, deque] = {}


def throttle(spec: str, rpm: int | None) -> None:
    """Client-side rate limit shared by all threads: at most `rpm` live requests per model per minute
    (free tiers enforce per-minute limits; bursting into them wastes the request and the time)."""
    if not rpm:
        return
    while True:
        with _RATE_LOCK:
            recent = _RECENT_CALLS.setdefault(spec, deque())
            now = time.time()
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) < rpm:
                recent.append(now)
                return
            wait = 60.5 - (now - recent[0])
        time.sleep(max(wait, 0.5))


class LLMError(RuntimeError):
    pass


def split_spec(spec: str) -> tuple[str, str]:
    provider, _, model = spec.partition("/")
    if provider not in PROVIDERS or not model:
        raise LLMError(f"Bad model spec {spec!r}. Use provider/model with provider in {sorted(PROVIDERS)}")
    return provider, model


def api_key(provider: str) -> str | None:
    env = PROVIDERS[provider].get("key_env")
    if not env:
        return None
    value = (os.environ.get(env) or "").strip()   # tolerate stray spaces pasted into .env
    return value or None


def extract_json(text: str) -> Any:
    """Pull a JSON value out of a model reply (tolerates code fences and leaked <thought> text)."""
    text = re.sub(r"<thought>.*?</thought>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("reply contains no JSON")
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[min(starts): end + 1])


class LLM:
    def __init__(self, spec: str, fallbacks: list[str] | tuple[str, ...] = (), *,
                 reasoning_effort: str | None = None, temperature: float | None = None,
                 timeout: float = 150, max_attempts: int = 2, cache_dir: str | Path = ROOT / ".cache" / "llm",
                 log: list[dict] | None = None, rpm: int | None = None):
        self.rpm = rpm
        self.chain = [spec, *[f for f in fallbacks if f != spec]]
        for s in self.chain:
            split_spec(s)
        self.reasoning_effort = reasoning_effort
        self.temperature = temperature
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log: list[dict] = log if log is not None else []
        self._no_json_mode: set[str] = set()
        self._dead: set[str] = set()      # models that timed out this run: skip them
        self._cooldown: dict[str, float] = {}   # models rate-limited/overloaded: skip until this time

    # ── public ────────────────────────────────────────────────────────────────
    def json(self, system: str, user: str, schema: type[BaseModel] | None = None,
             tag: str = "", max_tokens: int | None = None) -> Any:
        """Ask for JSON; return the parsed value (validated against `schema` when given)."""
        for spec in self.chain:                       # 1) answered before? (any model in chain)
            hit = self._cache_read(spec, system, user)
            if hit is not None:
                self.log.append({"tag": tag, "model": spec, "cached": True, "key": hit["key"],
                                 "in": hit["usage"]["in"], "out": hit["usage"]["out"], "seconds": 0.0})
                return self._parse(hit["text"], schema)

        errors: list[str] = []
        usable = [s for s in self.chain if s not in self._dead] or self.chain
        alive = [s for s in usable if self._cooldown.get(s, 0) <= time.time()]
        if not alive:                                 # everyone is cooling down: wait for the first
            time.sleep(max(0.5, min(min(self._cooldown.get(s, 0) for s in usable) - time.time(), 70)))
            alive = usable
        for spec in alive:                            # 2) live call, falling back down the chain
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            try:
                text, usage, secs = self._call_with_retries(spec, messages, max_tokens)
                try:
                    value = self._parse(text, schema)
                except (ValueError, ValidationError) as err:      # one repair round, same model
                    messages += [{"role": "assistant", "content": text},
                                 {"role": "user", "content": f"That reply was invalid: {str(err)[:600]}\n"
                                                             "Reply again with corrected JSON only."}]
                    text, usage2, secs2 = self._call_with_retries(spec, messages, max_tokens)
                    value = self._parse(text, schema)
                    usage = {k: usage[k] + usage2[k] for k in usage}
                    secs += secs2
            except (LLMError, ValueError, ValidationError) as err:
                errors.append(f"{spec}: {str(err)[:300]}")
                continue
            key = self._cache_write(spec, system, user, text, usage, tag, secs)
            self.log.append({"tag": tag, "model": spec, "cached": False, "key": key, "in": usage["in"],
                             "out": usage["out"], "seconds": round(secs, 1), "fell_back_from": errors[:]})
            return value
        raise LLMError(f"All models failed for {tag or 'request'}:\n  " + "\n  ".join(errors))

    def usage(self) -> dict:
        """Totals per model: live requests, cache hits, tokens, seconds."""
        out: dict[str, dict] = {}
        for c in self.log:
            m = out.setdefault(c["model"], {"requests": 0, "cache_hits": 0, "in": 0, "out": 0, "seconds": 0.0})
            m["cache_hits" if c["cached"] else "requests"] += 1
            if not c["cached"]:
                m["in"] += c["in"] or 0
                m["out"] += c["out"] or 0
                m["seconds"] = round(m["seconds"] + c["seconds"], 1)
        return out

    def raw_reply(self, key: str) -> dict | None:
        path = self.cache_dir / f"{key}.json"
        return json.loads(path.read_text()) if path.exists() else None

    # ── internals ─────────────────────────────────────────────────────────────
    @staticmethod
    def _parse(text: str, schema: type[BaseModel] | None) -> Any:
        value = extract_json(text)
        return schema.model_validate(value) if schema else value

    def _key(self, spec: str, system: str, user: str) -> str:
        blob = json.dumps([spec, system, user, self.reasoning_effort, self.temperature])
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def _cache_read(self, spec: str, system: str, user: str) -> dict | None:
        path = self.cache_dir / f"{self._key(spec, system, user)}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def _cache_write(self, spec, system, user, text, usage, tag, secs) -> str:
        key = self._key(spec, system, user)
        (self.cache_dir / f"{key}.json").write_text(json.dumps({
            "key": key, "model": spec, "tag": tag, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "seconds": round(secs, 1), "usage": usage, "reasoning_effort": self.reasoning_effort,
            "text": text}, indent=1, ensure_ascii=False))
        return key

    def _call_with_retries(self, spec: str, messages: list[dict], max_tokens: int | None):
        """Retry quota/overload errors briefly; never retry a timeout (an overloaded model costs a
        full timeout per attempt). A model that fails hard is skipped for the rest of the run."""
        for attempt in range(self.max_attempts):
            throttle(spec, self.rpm)
            t0 = time.time()
            try:
                text, usage = self._call(spec, messages, max_tokens)
                return text, usage, time.time() - t0
            except LLMError:
                raise
            except Exception as err:  # SDK errors differ per provider; classify generically
                status = getattr(err, "status_code", None)
                name = type(err).__name__
                timed_out = "Timeout" in name
                transient = status in RETRYABLE or "Connection" in name
                if timed_out or not transient or attempt == self.max_attempts - 1:
                    if timed_out:
                        self._dead.add(spec)                       # skip for the rest of the run
                    elif status in (429, 503):
                        self._cooldown[spec] = time.time() + 65    # per-minute limits reset
                    raise LLMError(f"{name} {status or ''} {str(err)[:200]}".strip()) from err
                time.sleep((20 if status == 429 else 5) * 2 ** attempt)
        raise LLMError("unreachable")

    def _call(self, spec: str, messages: list[dict], max_tokens: int | None) -> tuple[str, dict]:
        provider, model = split_spec(spec)
        key = api_key(provider)
        if PROVIDERS[provider].get("key_env") and not key and provider != "ollama":
            raise LLMError(f"missing {PROVIDERS[provider]['key_env']} (put it in .env)")

        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=key, timeout=self.timeout, max_retries=0)
            system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
            resp = client.messages.create(model=model, max_tokens=max_tokens or 16000, system=system,
                                          messages=[m for m in messages if m["role"] != "system"])
            text = "".join(getattr(b, "text", "") for b in resp.content)
            return text, {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}

        from openai import BadRequestError, OpenAI
        conf = PROVIDERS[provider]
        base_url = os.environ.get(conf["base_url_env"]) if "base_url_env" in conf else conf.get("base_url")
        client = OpenAI(api_key=key or "none", base_url=base_url, timeout=self.timeout, max_retries=0)
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if spec not in self._no_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self.reasoning_effort and conf.get("reasoning"):
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        try:
            resp = client.chat.completions.create(**kwargs)
        except BadRequestError as err:                 # some models reject JSON mode: retry without it
            if "response_format" in kwargs and spec not in self._no_json_mode:
                self._no_json_mode.add(spec)
                return self._call(spec, messages, max_tokens)
            raise LLMError(f"BadRequest: {str(err)[:300]}") from err
        u = resp.usage
        text = resp.choices[0].message.content or ""
        return text, {"in": getattr(u, "prompt_tokens", 0) or 0, "out": getattr(u, "completion_tokens", 0) or 0}


def list_models(provider: str) -> list[str]:
    """Model IDs the configured key can see (OpenAI-compatible /models or Anthropic)."""
    key = api_key(provider)
    if provider == "anthropic":
        import anthropic
        return sorted(m.id for m in anthropic.Anthropic(api_key=key).models.list())
    from openai import OpenAI
    conf = PROVIDERS[provider]
    base_url = os.environ.get(conf["base_url_env"]) if "base_url_env" in conf else conf.get("base_url")
    client = OpenAI(api_key=key or "none", base_url=base_url, timeout=30, max_retries=0)
    return sorted(m.id.removeprefix("models/") for m in client.models.list())


def probe(spec: str, timeout: float = 90) -> tuple[bool, float, str]:
    """Tiny live request (never cached): does this model answer right now?"""
    llm = LLM(spec, timeout=timeout, max_attempts=1, cache_dir=ROOT / ".cache" / "probe")
    t0 = time.time()
    try:
        text, _ = llm._call(spec, [{"role": "user", "content": 'Reply with JSON only: {"ok": true}'}], None)
        ok = extract_json(text).get("ok") is True
        return ok, time.time() - t0, "" if ok else f"unexpected reply: {text[:60]}"
    except Exception as err:  # noqa: BLE001 - report any failure
        return False, time.time() - t0, f"{type(err).__name__} {getattr(err, 'status_code', '') or ''}".strip()
