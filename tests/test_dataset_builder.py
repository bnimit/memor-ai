from memor.eval.dataset import build_counterfactual_cases
from memor.types import Artifact

def art(i, sid, text, t):
    return Artifact(id=i, kind="session_chunk", project="p", source="cc",
                    text=text, token_count=len(text.split()), created_at=t,
                    meta={"session_id": sid, "ord": int(i.split(":")[1])})

def test_build_cases_uses_first_turn_as_query_and_rest_as_need():
    # session s2 references "auth refresh" which session s1 already covered
    arts = [art("s1:0","s1","implemented auth refresh token rotation",10),
            art("s2:0","s2","the auth refresh token rotation is looping",100),
            art("s2:1","s2","root cause was reissuing token on 401",105)]
    cases = build_counterfactual_cases(arts, project="p", min_prior_sessions=1)
    assert len(cases) >= 1
    c = cases[0]
    assert "auth refresh" in c.query.lower()         # query = held-out session's opening turn
    assert "s1:0" in c.relevant_ids                   # prior session that should be recalled
    assert c.baseline_full_tokens > 0
