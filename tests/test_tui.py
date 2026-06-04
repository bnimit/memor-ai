import pytest
from memor.tui.app import MemorApp


@pytest.fixture
def app_fixture(tmp_path):
    from memor.store.sqlite_store import SqliteStore
    from memor.embed.fake import FakeEmbedder
    from memor.types import Artifact

    db_path = str(tmp_path / "test.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    arts = [
        Artifact(id="a1", kind="session_chunk", project="p", source="cc",
                 text="auth refresh token loop fix", token_count=6, created_at=100.0,
                 meta={"session_id": "s1", "role": "assistant", "ord": 0}),
        Artifact(id="a2", kind="memory", project="p", source="distill",
                 text="Use argon2 for password hashing", token_count=6, created_at=200.0,
                 meta={"session_id": "s1", "mem_type": "decision"}),
    ]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    return MemorApp(db_path=db_path, fake=True)


@pytest.mark.asyncio
async def test_app_starts_and_has_tabs(app_fixture):
    async with app_fixture.run_test() as pilot:
        app = pilot.app
        assert app.title == "Memor Inspector"
        tabs = app.query("TabbedContent")
        assert len(tabs) == 1
