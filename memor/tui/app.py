from __future__ import annotations
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TabbedContent, TabPane
from memor.store.sqlite_store import SqliteStore
from memor.tui.screens.query import QueryScreen
from memor.tui.screens.browse import BrowseScreen
from memor.tui.screens.eval_screen import EvalScreen
from memor.tui.screens.edges import EdgesScreen


def _init_embedder(fake: bool):
    if fake:
        from memor.embed.fake import FakeEmbedder
        return FakeEmbedder(dim=16)
    from memor.embed.local import LocalEmbedder
    return LocalEmbedder()


class MemorApp(App):
    CSS = """
    Screen { layout: vertical; }
    TabbedContent { height: 1fr; }
    """
    TITLE = "Memor Inspector"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("1", "tab_query", "Query"),
        ("2", "tab_browse", "Browse"),
        ("3", "tab_eval", "Eval"),
        ("4", "tab_edges", "Edges"),
    ]

    def __init__(self, db_path: str, fake: bool = False, *, store=None, embedder=None, **kwargs):
        super().__init__(**kwargs)
        self.embedder = embedder or _init_embedder(fake)
        self.store = store or SqliteStore(db_path, dim=self.embedder.dim)

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent("Query", "Browse", "Eval", "Edges"):
            with TabPane("Query", id="tab-query"):
                yield QueryScreen(self.store, self.embedder)
            with TabPane("Browse", id="tab-browse"):
                yield BrowseScreen(self.store)
            with TabPane("Eval", id="tab-eval"):
                yield EvalScreen(self.store)
            with TabPane("Edges", id="tab-edges"):
                yield EdgesScreen(self.store)
        yield Footer()

    def action_tab_query(self):
        self.query_one(TabbedContent).active = "tab-query"

    def action_tab_browse(self):
        self.query_one(TabbedContent).active = "tab-browse"

    def action_tab_eval(self):
        self.query_one(TabbedContent).active = "tab-eval"

    def action_tab_edges(self):
        self.query_one(TabbedContent).active = "tab-edges"
