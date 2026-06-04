from __future__ import annotations
from textual.app import ComposeResult
from textual.widgets import Static, Input, DataTable, Label
from textual.widget import Widget


class EdgesScreen(Widget):
    DEFAULT_CSS = """
    EdgesScreen { height: 1fr; layout: vertical; }
    #edge-input { dock: top; margin: 1 2; }
    #edge-stats { margin: 0 2; height: 1; }
    #edge-table { margin: 1 2; height: 1fr; }
    """

    def __init__(self, store, **kwargs):
        super().__init__(**kwargs)
        self.store = store

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Artifact ID (e.g. mem:s1:abc123)", id="edge-input")
        yield Label("", id="edge-stats")
        yield DataTable(id="edge-table")

    def on_mount(self):
        table = self.query_one("#edge-table", DataTable)
        table.add_columns("Direction", "Type", "Artifact ID", "Text")
        table.cursor_type = "row"
        edge_count = self.store.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        by_type = self.store.db.execute(
            "SELECT type, COUNT(*) FROM edges GROUP BY type"
        ).fetchall()
        summary = ", ".join(f"{t}: {c}" for t, c in by_type) if by_type else "none"
        self.query_one("#edge-stats", Label).update(
            f"{edge_count} total edges ({summary})"
        )

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "edge-input":
            return
        artifact_id = event.value.strip()
        if not artifact_id:
            return
        table = self.query_one("#edge-table", DataTable)
        table.clear()
        outgoing = self.store.db.execute(
            "SELECT e.type, a.id, a.text FROM edges e JOIN artifacts a ON e.dst_id = a.id WHERE e.src_id = ?",
            (artifact_id,),
        ).fetchall()
        incoming = self.store.db.execute(
            "SELECT e.type, a.id, a.text FROM edges e JOIN artifacts a ON e.src_id = a.id WHERE e.dst_id = ?",
            (artifact_id,),
        ).fetchall()
        for t, aid, txt in outgoing:
            table.add_row("->", t, aid[:30], (txt or "")[:80].replace("\n", " "))
        for t, aid, txt in incoming:
            table.add_row("<-", t, aid[:30], (txt or "")[:80].replace("\n", " "))
        if not outgoing and not incoming:
            self.query_one("#edge-stats", Label).update("No edges for this artifact")
