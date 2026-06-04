from __future__ import annotations
import json
from textual.app import ComposeResult
from textual.widgets import Static, DataTable, Label
from textual.widget import Widget


class EvalScreen(Widget):
    DEFAULT_CSS = """
    EvalScreen { height: 1fr; layout: vertical; }
    #eval-stats { margin: 1 2; height: 1; }
    #eval-table { margin: 1 2; height: 1fr; }
    """

    def __init__(self, store, **kwargs):
        super().__init__(**kwargs)
        self.store = store

    def compose(self) -> ComposeResult:
        yield Label("", id="eval-stats")
        yield DataTable(id="eval-table")

    def on_mount(self):
        table = self.query_one("#eval-table", DataTable)
        table.add_columns("Run", "Type", "k", "Project", "Recall@k", "nDCG@k", "Token Savings")
        table.cursor_type = "row"
        self._load_runs()

    def _load_runs(self):
        try:
            runs = self.store.db.execute(
                "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        except Exception:
            runs = []
        table = self.query_one("#eval-table", DataTable)
        table.clear()
        if not runs:
            self.query_one("#eval-stats", Label).update(
                "No eval runs yet. Run: memor eval <cases.json> or memor eval-judge --project <name>"
            )
            return
        self.query_one("#eval-stats", Label).update(f"{len(runs)} eval runs")
        for r in runs:
            config = json.loads(r["config"])
            metrics = json.loads(r["metrics"])
            eval_type = config.get("type", "counterfactual")
            k = str(config.get("k", "?"))
            project = config.get("project", "-")
            mem = metrics.get("memory", metrics)
            recall = f"{mem.get('recall@k', mem.get('mean_relevance', 0)):.3f}"
            ndcg = f"{mem.get('ndcg@k', 0):.3f}" if "ndcg@k" in mem else "-"
            savings = f"{mem.get('token_savings_vs_full', 0):.1%}" if "token_savings_vs_full" in mem else "-"
            table.add_row(str(r["id"]), eval_type, k, project, recall, ndcg, savings)
