import importlib


def test_bakeoff_imports_and_lists_candidates():
    m = importlib.import_module("scripts.bakeoff_distiller")
    assert isinstance(m.CANDIDATES, list) and len(m.CANDIDATES) >= 3
    assert all(len(c) == 2 for c in m.CANDIDATES)
    assert hasattr(m, "recall_latency_probe")
