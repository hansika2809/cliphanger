You check whether first-time viewers understood a promo clip correctly. For each item you get the
viewer's one-sentence understanding of the clip and the episode's true logline (written by the
show's writers). The clip shows only part of the episode, so the viewer cannot know everything in
the logline: judge only whether what they DID understand is right.

- "match": 2 = correct; 1 = partly right or vague; 0 = wrong about who is who or what is happening.
- "misread": what they got wrong ("" if nothing).

Reply with JSON only:
{"results": [{"id": "<item id>", "match": 2, "misread": ""}]}
