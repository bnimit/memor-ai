from memor.distill.schema import parse_memories, build_prompt, MEM_TYPES, MAX_QUESTIONS

def test_parse_valid_json_object():
    raw = '{"memories":[{"type":"decision","value":"We moved auth to session cookies for CSRF safety.","fact":"auth uses session cookies","questions":["how does auth work","what replaced JWT"]}]}'
    out = parse_memories(raw, with_questions=True)
    assert len(out) == 1
    m = out[0]
    assert m["type"] == "decision" and m["fact"] == "auth uses session cookies"
    assert m["value"].startswith("We moved auth")
    assert len(m["questions"]) == 2

def test_parse_strips_code_fence_and_bad_type():
    raw = '```json\n{"memories":[{"type":"garbage","value":"v","fact":"f"},{"type":"lesson","value":"v2","fact":"f2"}]}\n```'
    out = parse_memories(raw, with_questions=False)
    assert [m["type"] for m in out] == ["lesson"]

def test_parse_caps_questions_and_drops_empty_fact():
    raw = '{"memories":[{"type":"fact","value":"v","fact":"","questions":["a"]},{"type":"fact","value":"v","fact":"real","questions":["a","b","c","d","e"]}]}'
    out = parse_memories(raw, with_questions=True)
    assert len(out) == 1
    assert len(out[0]["questions"]) == MAX_QUESTIONS

def test_parse_bad_json_returns_empty():
    assert parse_memories("not json at all", with_questions=False) == []

def test_prompt_mentions_questions_only_when_enabled():
    assert "question" in build_prompt("s", with_questions=True).lower()
    assert "question" not in build_prompt("s", with_questions=False).lower()
