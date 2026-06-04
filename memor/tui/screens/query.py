from __future__ import annotations
from textual.app import ComposeResult
from textual.widgets import Static, Input, DataTable, Label
from textual.containers import Vertical, Horizontal
from textual.widget import Widget
from memor.retrieve.retriever import Retriever
from memor.types import Scope


class QueryScreen(Widget):
    DEFAULT_CSS = """
    QueryScreen { height: 1fr; layout: vertical; }
    #query-input { dock: top; margin: 1 2; }
    #query-stats { dock: top; margin: 0 2; height: 1; }
    #query-results { margin: 1 2; height: 1fr; }
    """

    def __init__(self, store, embedder, **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.embedder = embedder

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Query: what did we decide about auth?", id="query-input")
        yield Label("", id="query-stats")
        yield DataTable(id="query-results")

    def on_mount(self):
        table = self.query_one("#query-results", DataTable)
        table.add_columns("Rank", "Score", "Kind", "Project", "Text")
        table.cursor_type = "row"

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "query-input":
            return
        query_text = event.value.strip()
        if not query_text:
            return
        r = Retriever(self.store, self.embedder, k=8, recency_weight=0.2, edge_expand=True)
        trace = r.query(query_text, Scope())
        table = self.query_one("#query-results", DataTable)
        table.clear()
        for i, h in enumerate(trace.hits, 1):
            a = h.artifact
            text_preview = a.text[:120].replace("\n", " ")
            table.add_row(str(i), f"{h.score:.3f}", a.kind, a.project or "-", text_preview)
        tokens = sum(h.artifact.token_count for h in trace.hits)
        self.query_one("#query-stats", Label).update(
            f"{len(trace.hits)} hits | {trace.latency_ms:.1f}ms | {tokens} tokens"
        )
