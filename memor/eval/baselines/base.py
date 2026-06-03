from __future__ import annotations
from memor.types import Artifact


class ExternalBaseline:
    name: str = ""

    def available(self) -> bool:
        raise NotImplementedError

    def index(self, artifacts: list[Artifact]) -> None:
        raise NotImplementedError

    def retrieve(self, query: str, project: str, k: int) -> list[str]:
        raise NotImplementedError
