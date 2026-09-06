"""Agent desk panes + filtered savings series."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from memor.embed.fake import FakeEmbedder
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


def _seed(tmp_path):
    db_path = str(tmp_path / "desk.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="a1",
        kind="memory",
        project="p1",
        source="distill",
        text="use bcrypt",
        token_count=4,
        created_at=100.0,
        meta={"mem_type": "decision"},
    )
    s.add_artifacts([art], e.embed([art.text]))
    s.log_recall("p1", "hashing", 2, 0.9, 80, 20.0, "ok", "s1", agent="claude")
    s.log_recall("p1", "cursor q", 1, 0.7, 40, 18.0, "ok", "s2", agent="cursor")
    s.log_recall("p1", "miss", 0, 0.0, 0, 10.0, "no_hits", "s3", agent="cursor")
    now = time.time()
    s.record_proxy_savings({
        "timestamp": now - 86400,
        "agent": "cursor",
        "provider": "openai",
        "session_id": "w1",
        "tokens_before": 1000,
        "tokens_after": 600,
        "content_types": {"text": 1},
        "passthrough": 0,
    })
    s.record_proxy_savings({
        "timestamp": now,
        "agent": "cursor",
        "provider": "openai",
        "session_id": "w2",
        "tokens_before": 500,
        "tokens_after": 400,
        "content_types": {"log": 1},
        "passthrough": 0,
    })
    from memor.dashboard.server import create_app
    return create_app(db_path), s


def test_agent_desk_endpoint(tmp_path):
    app, _ = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/api/agent-desk?agent=cursor")
    assert r.status_code == 200
    data = r.json()
    assert data["stats"]["agent"] == "cursor"
    assert data["stats"]["recalls"] == 2
    assert data["stats"]["hits"] == 1
    assert data["stats"]["proxy"]["tokens_before"] == 1500
    assert data["stats"]["proxy"]["pct_saved"] > 0
    assert len(data["recalls"]) == 2
    assert data["savings_series"][-1]["cumulative_saved"] == 500  # 400 + 100


def test_recall_trend_filter_by_agent(tmp_path):
    app, _ = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/api/recall-trend?days=30&agent=cursor")
    assert r.status_code == 200
    rows = r.json()
    assert sum(row["recalls"] for row in rows) == 2


def test_savings_ledger_includes_cumulative(tmp_path):
    app, _ = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/api/savings-ledger?days=30&agent=cursor")
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["tokens_before"] == 1500
    assert data["per_day"][-1]["cumulative_saved"] == 500


def test_dashboard_html_has_desk_tabs(tmp_path):
    app, _ = _seed(tmp_path)
    client = TestClient(app)
    html = client.get("/").text
    assert "desk-tabs" in html
    assert "pane-overview" in html
    assert "pane-agent" in html
    # Was "Cumulative tokens saved" while showing a rolling 30d window, so the
    # figure fell as old traffic aged out and read as a regression. Naming the
    # window in the title was not enough either: the big number was still the
    # 30d one. The panel is now titled for the lifetime total it leads with.
    assert "Tokens saved" in html
    assert "(last 30d)" not in html
    assert "cum-saved-lifetime" in html
    assert "badge-cursor" in html
    # The Cursor wire MITM was removed — no chip, colour, or label may survive.
    assert "cursor-wire" not in html
    assert "cursor_wire" not in html


def test_recall_worth_endpoint(tmp_path):
    """The episode meter must never take the dashboard down, even with no data."""
    app, _ = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/api/recall-worth")
    assert r.status_code == 200
    data = r.json()
    assert "overall" in data
    assert data["overall"]["verdict"] in {
        "insufficient_data", "no_effect", "saves", "costs",
    }


def test_dashboard_html_has_recall_worth_panel(tmp_path):
    app, _ = _seed(tmp_path)
    html = TestClient(app).get("/").text
    assert "recall-worth-panel" in html
    assert "Does recall reduce work?" in html
    # A null must be presentable, not hidden.
    assert "no measurable effect" in html


def test_compression_endpoint(tmp_path):
    app, _ = _seed(tmp_path)
    d = TestClient(app).get("/api/compression").json()
    assert "enabled" in d and "realized" in d
    assert d["liveness"]["state"] in {
        "off", "on", "pending", "live", "not_taking_effect",
    }


def test_dashboard_html_has_compression_panel(tmp_path):
    app, _ = _seed(tmp_path)
    html = TestClient(app).get("/").text
    assert "compression-panel" in html
    assert "cx-live" in html
    # The liveness warning must be presentable, not buried in a log.
    assert "NOT taking effect" in html


def _contrib_seed(tmp_path):
    """A store where one agent writes and another only reads."""
    db_path = str(tmp_path / "contrib.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    arts = [
        Artifact(id="c1", kind="session_chunk", project="alpha", source="codex",
                 text="the root cause was a missing index", token_count=40,
                 created_at=500.0, meta={"agent": "codex"}),
        Artifact(id="c2", kind="session_chunk", project="beta", source="codex",
                 text="we switched to a queue", token_count=10,
                 created_at=900.0, meta={"agent": "codex"}),
        # Claude's oldest chunks predate meta.agent and carry only source.
        Artifact(id="k1", kind="session_chunk", project="alpha",
                 source="claude_code", text="older claude chunk", token_count=7,
                 created_at=300.0, meta={}),
    ]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    s.log_recall("alpha", "q", 1, 0.8, 20, 5.0, "ok", "s1", agent="cursor")
    from memor.dashboard.server import create_app
    return create_app(db_path), s


def test_agent_desk_reports_what_the_agent_wrote(tmp_path):
    """Recalls measure consumption; a shared layer also has to show supply.

    Every desk KPI was a read metric, so an agent that draws on the store
    without ever feeding it looked identical to one that does both. That is
    precisely how Codex went months serving recalls while contributing nothing.
    """
    app, _ = _contrib_seed(tmp_path)
    c = TestClient(app).get("/api/agent-desk?agent=codex").json()["contribution"]
    assert c["chunks"] == 2
    assert c["tokens"] == 50
    assert c["projects"] == 2
    assert c["last_seen"] == 900.0
    assert [p["project"] for p in c["by_project"]] == ["alpha", "beta"]


def test_claude_contribution_counts_pre_meta_chunks(tmp_path):
    """Claude's 24k oldest rows carry source='claude_code' and no meta.agent.

    Reading meta alone would report the busiest agent as having written nothing.
    """
    app, _ = _contrib_seed(tmp_path)
    c = TestClient(app).get("/api/agent-desk?agent=claude").json()["contribution"]
    assert c["chunks"] == 1


def test_read_only_agent_is_visibly_empty(tmp_path):
    """A consumer must render as a zero, not as missing data."""
    app, _ = _contrib_seed(tmp_path)
    c = TestClient(app).get("/api/agent-desk?agent=cursor").json()["contribution"]
    assert c["chunks"] == 0 and c["by_project"] == []


def test_overview_is_split_from_the_evidence_behind_it(tmp_path):
    """13 sections and 6 tables on one scroll buried the daily figures.

    The measurement pane holds the evidence; the overview keeps the KPIs. Both
    must exist, and long tables must collapse rather than be truncated away.
    """
    app, _ = _seed(tmp_path)
    html = TestClient(app).get("/").text
    assert 'id="pane-measure"' in html
    assert "show-all-btn" in html
    # Evidence sections moved off the overview, not deleted.
    for probe in ("recall-baseline-section", "quality-section", "proxy-savings-section"):
        assert probe in html
    overview = html.split('id="pane-overview"')[1].split('id="pane-measure"')[0]
    assert overview.count("<section") <= 7
    assert "recall-baseline-section" not in overview
    assert "quality-section" not in overview


def test_efficiency_carries_its_own_latency(tmp_path):
    """A panel must not depend on an endpoint its pane does not load.

    The Efficiency card read p50 latency from /api/summary. Moving the card to
    the measurement pane, which loads only /api/efficiency, blanked the figure
    without any request failing -- the exact failure that is invisible in tests
    that assert on endpoints rather than on what a pane can actually render.
    """
    app, _ = _seed(tmp_path)
    d = TestClient(app).get("/api/efficiency").json()
    assert d["total_recalls"] > 0
    assert d["p50_latency_ms"] > 0


def _pane_of(html: str) -> dict:
    """Map every element id to the pane that actually contains it.

    Substring checks on the raw HTML cannot tell a moved section from a
    duplicated one, and the sections were relocated by a script. This parses
    the real nesting instead.
    """
    from html.parser import HTMLParser

    class _P(HTMLParser):
        VOID = {"br", "img", "input", "meta", "link", "hr"}

        def __init__(self):
            super().__init__()
            self.stack: list = []
            self.owner: dict = {}

        def handle_starttag(self, tag, attrs):
            el_id = dict(attrs).get("id")
            pane = next(
                (x for x in reversed(self.stack) if x and x.startswith("pane-")), None
            )
            if el_id:
                self.owner[el_id] = pane
            if tag not in self.VOID:
                self.stack.append(el_id)

        def handle_endtag(self, tag):
            if self.stack:
                self.stack.pop()

    p = _P()
    p.feed(html)
    return p.owner


def test_moved_sections_are_nested_in_the_pane_that_loads_them(tmp_path):
    """A section rendered under a pane whose loader never runs stays blank.

    That is not hypothetical: the efficiency card kept its markup and lost its
    number this way, and nothing failed loudly. Parentage is asserted in the
    parsed DOM rather than by substring, which would pass just as happily on a
    section accidentally left behind in the overview.
    """
    app, _ = _seed(tmp_path)
    owner = _pane_of(TestClient(app).get("/").text)
    for el_id in ("savings-periods-section", "recall-baseline-section",
                  "quality-section", "proxy-savings-section", "trend-chart",
                  "e-latency"):
        assert owner.get(el_id) == "pane-measure", el_id
    for el_id in ("projects-body", "recalls-body", "savings-equity",
                  "cum-saved-lifetime", "agent-compare-section"):
        assert owner.get(el_id) == "pane-overview", el_id
    assert owner.get("ap-contrib-body") == "pane-agent"


def test_the_split_did_not_duplicate_or_drop_any_element(tmp_path):
    """Sections were relocated by a text splice, which can do both silently."""
    import collections
    import re

    app, _ = _seed(tmp_path)
    html = TestClient(app).get("/").text
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert [i for i, n in collections.Counter(ids).items() if n > 1] == []
    assert html.count("<section") == html.count("</section>")


def test_hero_leads_with_the_total_that_never_falls(tmp_path):
    """The big number must be lifetime, not a rolling window.

    Labelling the window was not sufficient. A panel called "Tokens saved"
    whose headline is 30 days still reads as *the* figure, and still drops when
    a quiet fortnight ages old traffic out -- which is exactly what was
    reported, twice, after the first fix. Lifetime is monotonic, so it leads
    and the window becomes context beneath it.
    """
    import re

    db_path = str(tmp_path / "hero.db")
    s = SqliteStore(db_path, dim=16)
    now = time.time()
    # Old traffic, far outside the 30d window, carrying most of the savings.
    s.record_proxy_savings({
        "timestamp": now - 90 * 86400, "agent": "claude", "provider": "anthropic",
        "session_id": "old", "tokens_before": 1_000_000, "tokens_after": 100_000,
        "content_types": {"log": 1}, "passthrough": 0,
    })
    s.record_proxy_savings({
        "timestamp": now, "agent": "claude", "provider": "hook",
        "session_id": "new", "tokens_before": 10_000, "tokens_after": 1_000,
        "content_types": {"log": 1}, "passthrough": 0,
    })
    from memor.dashboard.server import create_app

    client = TestClient(create_app(db_path))
    data = client.get("/api/savings-ledger?days=30").json()
    assert data["lifetime"]["tokens_saved"] == 909_000
    assert data["summary"]["tokens_saved"] == 9_000

    html = client.get("/").text
    js = html.split("function renderSavingsHero")[1].split("\n  function ")[0]
    # The first assignment is the empty-state dash; the live one is the last.
    writes = re.findall(
        r"getElementById\('cum-saved-big'\)\.textContent\s*=\s*([^;]+);", js)
    assert writes, "hero never writes the headline"
    assert "lifetimeSaved" in writes[-1], writes[-1]


def test_hero_heading_and_value_do_not_disagree(tmp_path):
    """The heading promises lifetime, so the value may only ever be lifetime.

    Falling back to the 30d total when lifetime is missing would print a
    window's figure under an all-time heading -- the same swap this panel has
    been fixed for twice.
    """
    app, _ = _seed(tmp_path)
    html = TestClient(app).get("/").text
    assert "Tokens saved (lifetime)" in html
    js = html.split("function renderSavingsHero")[1].split("\n  function ")[0]
    live = js.split("getElementById('cum-saved-big').textContent =")[-1].split(";")[0]
    assert "lifetimeSaved" in live
    assert "cum" not in live, live
    # And the value must not restate the qualifier the heading already carries.
    assert "saved all time" not in html


def test_health_reports_how_long_each_compression_path_has_been_silent(tmp_path):
    """A stopped service and a quiet week render identically without this.

    On the development machine the launchd services were not running between
    22 Aug and 6 Sep. Claude ran daily throughout. The only symptom anywhere in
    the product was a savings curve that stopped moving -- which is exactly what
    a genuinely quiet fortnight looks like. The dashboard could not tell the
    user which of the two had happened, so it said nothing at all.
    """
    db_path = str(tmp_path / "idle.db")
    s = SqliteStore(db_path, dim=16)
    now = time.time()
    s.record_proxy_savings({
        "timestamp": now - 20 * 86400, "agent": "claude", "provider": "anthropic",
        "session_id": "old", "tokens_before": 1000, "tokens_after": 100,
        "content_types": {"log": 1}, "passthrough": 0,
    })
    s.record_proxy_savings({
        "timestamp": now, "agent": "claude", "provider": "hook",
        "session_id": "new", "tokens_before": 500, "tokens_after": 50,
        "content_types": {"log": 1}, "passthrough": 0,
    })
    from memor.dashboard.server import create_app

    paths = TestClient(_isolated(create_app(db_path))).get(
        "/api/health").json()["compression_paths"]
    assert paths["proxy"]["idle_days"] >= 19
    assert paths["hook"]["idle_days"] < 1


def test_a_path_that_never_ran_is_not_reported_as_stale(tmp_path):
    """Never-configured and stopped are different problems.

    Reporting an absent path as a regression would fire the warning on every
    fresh install, which is the fastest way to teach someone to ignore it.
    """
    db_path = str(tmp_path / "fresh.db")
    SqliteStore(db_path, dim=16)
    from memor.dashboard.server import create_app

    paths = TestClient(_isolated(create_app(db_path))).get(
        "/api/health").json()["compression_paths"]
    assert paths["proxy"]["idle_days"] is None
    assert paths["hook"]["idle_days"] is None


def _isolated(app):
    """Detach health from this machine's real proxy and Claude settings."""
    app.state.proxy_started_at = lambda: None
    app.state.hook_installed_at = lambda: None
    return app


def test_a_reinstalled_path_is_not_reported_as_idle(tmp_path, monkeypatch):
    """Fixing the problem must clear the warning, or the warning is noise.

    Reinstalling a proxy writes no ledger row -- the next real request does.
    Measuring idleness from the last row alone left the banner up after the
    user had already done what it asked, which is exactly how a banner teaches
    people to ignore banners. It happened: the proxy was reinstalled, reported
    healthy on :8421, and the dashboard still said 15.4 days.
    """
    db_path = str(tmp_path / "idle.db")
    s = SqliteStore(db_path, dim=16)
    now = time.time()
    s.record_proxy_savings({
        "timestamp": now - 20 * 86400, "agent": "claude", "provider": "anthropic",
        "session_id": "old", "tokens_before": 1000, "tokens_after": 100,
        "content_types": {"log": 1}, "passthrough": 0,
    })

    from memor.dashboard.server import create_app

    app = _isolated(create_app(db_path))
    # Nothing answering on the proxy port: the row is all we have, so 20 days.
    stale = TestClient(app).get("/api/health").json()["compression_paths"]["proxy"]
    assert stale["idle_days"] >= 19

    # The same store, but a proxy that just came up: the clock resets even
    # though no new row exists yet. This is the case the user hit.
    app.state.proxy_started_at = lambda: now
    fresh = TestClient(app).get("/api/health").json()["compression_paths"]["proxy"]
    assert fresh["idle_days"] == 0.0, "reinstalling must clear the warning"


def test_a_path_that_never_ran_is_still_not_reported_as_stale(tmp_path):
    """Never-configured stays distinct from stopped, after the restart change."""
    db_path = str(tmp_path / "fresh.db")
    SqliteStore(db_path, dim=16)
    from memor.dashboard.server import create_app

    paths = TestClient(_isolated(create_app(db_path))).get(
        "/api/health").json()["compression_paths"]
    assert paths["proxy"]["idle_days"] is None
    assert paths["hook"]["idle_days"] is None


def test_a_long_running_path_that_records_nothing_still_warns(tmp_path):
    """Measuring from restart must not create a blind spot.

    Taking max(last_row, restart) fixes the false alarm after a reinstall, but
    it would be worse than useless if a restart silenced the warning forever: a
    proxy that has been up for weeks recording nothing is exactly the failure
    this banner exists to surface. The restart resets the clock; it does not
    stop it.
    """
    db_path = str(tmp_path / "long.db")
    s = SqliteStore(db_path, dim=16)
    now = time.time()
    s.record_proxy_savings({
        "timestamp": now - 40 * 86400, "agent": "claude", "provider": "anthropic",
        "session_id": "old", "tokens_before": 1000, "tokens_after": 100,
        "content_types": {"log": 1}, "passthrough": 0,
    })
    from memor.dashboard.server import create_app

    app = _isolated(create_app(db_path))

    # Up for ten days, still no rows: that is a real fault and must be said.
    app.state.proxy_started_at = lambda: now - 10 * 86400
    assert TestClient(app).get("/api/health").json()[
        "compression_paths"]["proxy"]["idle_days"] == 10.0

    # Not answering at all: fall back to the ledger, which is older still.
    app.state.proxy_started_at = lambda: None
    assert TestClient(app).get("/api/health").json()[
        "compression_paths"]["proxy"]["idle_days"] >= 39


def test_a_long_uptime_does_not_outrank_recent_activity(tmp_path):
    """A proxy running since before its newest row is healthy, not idle.

    This is the case that separates max(last, restart) from a naive
    "restart wins": a proxy up for 30 days that recorded a row an hour ago is
    working perfectly. Preferring the restart time would report it as 30 days
    idle and fire the warning on a completely healthy path.
    """
    db_path = str(tmp_path / "uptime.db")
    s = SqliteStore(db_path, dim=16)
    now = time.time()
    s.record_proxy_savings({
        "timestamp": now - 3600, "agent": "claude", "provider": "anthropic",
        "session_id": "recent", "tokens_before": 1000, "tokens_after": 100,
        "content_types": {"log": 1}, "passthrough": 0,
    })
    from memor.dashboard.server import create_app

    app = _isolated(create_app(db_path))
    app.state.proxy_started_at = lambda: now - 30 * 86400

    idle = TestClient(app).get("/api/health").json()[
        "compression_paths"]["proxy"]["idle_days"]
    assert idle == 0.0, "recent activity must win over a long uptime"
