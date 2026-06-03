from memorable.eval.baselines.base import ExternalBaseline
from memorable.types import Artifact


class GraphitiBaseline(ExternalBaseline):
    """Adapter for Zep Graphiti. Requires `pip install graphiti-core` + a Neo4j/FalkorDB
    instance. Behind a flag; skipped when unavailable so the suite still runs."""

    name = "graphiti"

    def __init__(self):
        self._client = None

    def available(self) -> bool:
        try:
            import graphiti_core  # noqa
            return True
        except Exception:
            return False

    def index(self, artifacts: list[Artifact]) -> None:
        raise NotImplementedError("wire to graphiti_core.add_episode per artifact")

    def retrieve(self, query: str, project: str, k: int) -> list[str]:
        raise NotImplementedError("wire to graphiti_core search; map results back to artifact ids")
