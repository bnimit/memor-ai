from __future__ import annotations
import json
from textual.app import ComposeResult
from textual.widgets import Static, DataTable, Input, Label, Select
from textual.containers import Vertical, Horizontal
from textual.widget import Widget


class BrowseScreen(Widget):
    DEFAULT_CSS = """
    BrowseScreen { height: 1fr; layout: vertical; }
    #browse-filters { dock: top; height: 3; layout: horizontal; margin: 1 2; }
    #browse-filters Input { width: 1fr; }
    #browse-filters Select { width: 20; }
    #browse-stats { margin: 0 2; height: 1; }
    #browse-table { margin: 1 2; height: 1fr; }
    """

    def __init__(self, store, **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.page = 0
        self.page_size = 50

    def compose(self) -> ComposeResult:
        projects = [r[0] for r in self.store.db.execute(
            "SELECT DISTINCT project FROM artifacts").fetchall()]
        kinds = [r[0] for r in self.store.db.execute(
            "SELECT DISTINCT kind FROM artifacts").fetchall()]
        with Horizontal(id="browse-filters"):
            yield Select(
                [(p, p) for p in ["all"] + projects],
                value="all", id="browse-project",
            )
            yield Select(
                [(k, k) for k in ["all"] + kinds],
                value="all", id="browse-kind",
            )
            yield Input(placeholder="Search text...", id="browse-search")
        yield Label("", id="browse-stats")
        yield DataTable(id="browse-table")

    def on_mount(self):
        table = self.query_one("#browse-table", DataTable)
        table.add_columns("ID", "Kind", "Project", "Tokens", "Text")
        table.cursor_type = "row"
        self._refresh_data()

    def on_select_changed(self, event):
        self._refresh_data()

    def on_input_submitted(self, event):
        if event.input.id == "browse-search":
            self._refresh_data()

    def _refresh_data(self):
        where = ["active = 1"]
        params = []
        try:
            project = self.query_one("#browse-project", Select).value
            if project != "all" and project is not Select.BLANK:
                where.append("project = ?"); params.append(project)
        except Exception:
            pass
        try:
            kind = self.query_one("#browse-kind", Select).value
            if kind != "all" and kind is not Select.BLANK:
                where.append("kind = ?"); params.append(kind)
        except Exception:
            pass
        try:
            search = self.query_one("#browse-search", Input).value
            if search:
                where.append("text LIKE ?"); params.append(f"%{search}%")
        except Exception:
            pass

        count = self.store.db.execute(
            f"SELECT COUNT(*) FROM artifacts WHERE {' AND '.join(where)}", params
        ).fetchone()[0]
        self.query_one("#browse-stats", Label).update(f"{count} artifacts")

        rows = self.store.db.execute(
            f"SELECT * FROM artifacts WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [self.page_size, self.page * self.page_size],
        ).fetchall()
        table = self.query_one("#browse-table", DataTable)
        table.clear()
        for r in rows:
            text_preview = (r["text"] or "")[:100].replace("\n", " ")
            table.add_row(r["id"][:20], r["kind"], r["project"] or "-",
                          str(r["token_count"]), text_preview)
