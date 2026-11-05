"""LLM layer tests: no network. `_call` is replaced by fakes."""
import pytest
from pydantic import BaseModel

from cliphanger.llm import LLM, LLMError, extract_json


class Reply(BaseModel):
    ok: bool


class Boom(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_extract_json_tolerates_fences_thoughts_and_prose():
    assert extract_json('{"ok": true}') == {"ok": True}
    assert extract_json('```json\n{"ok": true}\n```') == {"ok": True}
    assert extract_json('<thought>hmm {not json}</thought>{"ok": true}') == {"ok": True}
    assert extract_json('Sure! Here it is: {"a": [1, 2]} hope that helps') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_falls_back_to_next_model_then_serves_from_cache(tmp_path, monkeypatch):
    calls = []

    def fake_call(self, spec, messages, max_tokens):
        calls.append(spec)
        if spec == "gemini/flaky":
            raise Boom(503)
        return '{"ok": true}', {"in": 10, "out": 2}

    monkeypatch.setattr(LLM, "_call", fake_call)
    llm = LLM("gemini/flaky", ["gemini/steady"], max_attempts=1, cache_dir=tmp_path)
    assert llm.json("sys", "user", schema=Reply, tag="t").ok is True
    assert calls == ["gemini/flaky", "gemini/steady"]
    assert llm.log[-1]["model"] == "gemini/steady" and llm.log[-1]["fell_back_from"]

    calls.clear()
    assert llm.json("sys", "user", schema=Reply, tag="t").ok is True   # cache hit: no live call
    assert calls == [] and llm.log[-1]["cached"] is True
    assert llm.usage()["gemini/steady"] == {"requests": 1, "cache_hits": 1, "in": 10, "out": 2, "seconds": 0.0}


def test_invalid_json_gets_one_repair_round(tmp_path, monkeypatch):
    replies = iter(['{"ok": "maybe"}', '{"ok": true}'])
    monkeypatch.setattr(LLM, "_call", lambda self, spec, messages, mt: (next(replies), {"in": 1, "out": 1}))
    llm = LLM("gemini/m", cache_dir=tmp_path)
    assert llm.json("s", "u", schema=Reply).ok is True
    assert llm.log[-1]["in"] == 2   # both rounds counted


def test_all_models_failing_raises_with_reasons(tmp_path, monkeypatch):
    def always_429(self, spec, messages, mt):
        raise Boom(429)
    monkeypatch.setattr(LLM, "_call", always_429)
    monkeypatch.setattr("time.sleep", lambda s: None)
    llm = LLM("gemini/a", ["groq/b"], max_attempts=2, cache_dir=tmp_path)
    with pytest.raises(LLMError, match="gemini/a.*\n.*groq/b"):
        llm.json("s", "u")


def test_timeout_is_not_retried_and_the_model_is_skipped_afterwards(tmp_path, monkeypatch):
    class APITimeoutError(Exception):
        pass

    calls = []

    def fake_call(self, spec, messages, mt):
        calls.append(spec)
        if spec == "gemini/slow":
            raise APITimeoutError("timed out")
        return '{"ok": true}', {"in": 1, "out": 1}

    monkeypatch.setattr(LLM, "_call", fake_call)
    llm = LLM("gemini/slow", ["gemini/fast"], max_attempts=3, cache_dir=tmp_path)
    llm.json("s", "first")
    llm.json("s", "second")
    assert calls == ["gemini/slow", "gemini/fast", "gemini/fast"]   # one timeout, then skipped


def test_bad_spec_is_rejected():
    with pytest.raises(LLMError):
        LLM("not-a-provider/model")
