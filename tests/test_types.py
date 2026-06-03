from memor.types import Artifact, Scope, Hit, RetrievalTrace

def test_artifact_defaults_and_scope_match():
    a = Artifact(id="a1", kind="session_chunk", project="stablex",
                 source="cc", text="auth refresh loop", token_count=4,
                 created_at=100.0, meta={})
    assert a.kind == "session_chunk"
    s = Scope(project="stablex", kinds=["session_chunk"])
    assert s.matches(a) is True
    assert Scope(project="other").matches(a) is False
    assert Scope(since=200.0).matches(a) is False  # a.created_at=100 < 200
