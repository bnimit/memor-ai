"""Heuristic supersession — local, no LLM. Pure helpers live here; the store
owns persistence and the distiller owns detection wiring."""
from __future__ import annotations

VALIDITY_FLOOR = 0.25

def validity_for(n_active_disputers: int) -> float:
    if n_active_disputers <= 0:
        return 1.0
    return max(VALIDITY_FLOOR, 0.5 ** n_active_disputers)
