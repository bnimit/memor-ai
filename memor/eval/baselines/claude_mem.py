from memor.eval.baselines.base import ExternalBaseline
from memor.types import Artifact
import os


class ClaudeMemBaseline(ExternalBaseline):
    """Adapter for the claude-mem plugin store. Behind a flag; skipped when its
    store path is absent so the suite still runs."""

    name = "claude-mem"

    def available(self) -> bool:
        return os.path.exists(os.path.expanduser("~/.claude-mem"))

    def index(self, artifacts: list[Artifact]) -> None:
        raise NotImplementedError("point at the claude-mem store or re-ingest via its API")

    def retrieve(self, query: str, project: str, k: int) -> list[str]:
        raise NotImplementedError("query claude-mem; map results back to artifact ids")
