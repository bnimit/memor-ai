from memor.temporal import half_life_days, mem_type_of, DEFAULT_HALF_LIFE_DAYS
from memor.types import Artifact

def _art(kind, mem_type=None):
    meta = {"mem_type": mem_type} if mem_type else {}
    return Artifact(id="x", kind=kind, project="p", source="s",
                    text="t", token_count=1, created_at=0.0, meta=meta)

def test_half_life_per_type():
    assert half_life_days("decision") == 90
    assert half_life_days("global") == 180
    assert half_life_days("extract") == 21
    assert half_life_days("session_chunk") == 14
    assert half_life_days("research") == 120
    assert half_life_days("totally-unknown") == DEFAULT_HALF_LIFE_DAYS

def test_mem_type_of_prefers_meta_then_kind():
    assert mem_type_of(_art("memory", "decision")) == "decision"
    assert mem_type_of(_art("research")) == "research"
    assert mem_type_of(_art("memory")) == "memory"
