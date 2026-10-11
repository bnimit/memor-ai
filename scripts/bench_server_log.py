#!/usr/bin/env python3
"""Generate a synthetic server log and measure what memor's log crusher does to it.

The README's compression figures come from coding sessions, where tool output is
a third of a request and most of it is source the compressor must not touch.
Log-heavy workloads are the other denominator: a server log is almost all
repetitive INFO/DEBUG traffic, so it is where a line-dropping compressor should
look its best. This script builds that case on purpose and reports two things,
because a saving alone proves nothing:

1. **Savings**: cl100k tokens before and after, through both ``compress_text``
   and the PostToolUse hook path an agent's ``cat server.log`` takes.
2. **Retention**: whether the lines an engineer debugging the incident would
   need survived: every ERROR and WARN, the incident's onset, the 5xx access
   lines, the traceback frames, and the INFO line that actually caused it.

The log is seeded and deterministic, so a change to the crusher shows up as a
change in these numbers rather than in the fixture.

Usage:
    python -m scripts.bench_server_log                    # report on the default log
    python -m scripts.bench_server_log --write out.log    # also save the raw log
    python -m scripts.bench_server_log --variant quiet    # no incident
    python -m scripts.bench_server_log --variant errfield # metrics carry errors=0
"""
from __future__ import annotations

import argparse
import json
import random
import re
from datetime import datetime, timedelta, timezone

PATHS = [
    ("GET", "/api/v1/orders/{id}", 0.30),
    ("GET", "/api/v1/orders?customer_id={cid}&limit=50", 0.18),
    ("POST", "/api/v1/orders", 0.10),
    ("GET", "/api/v1/customers/{cid}", 0.14),
    ("PATCH", "/api/v1/orders/{id}/status", 0.06),
    ("GET", "/api/v1/products/{pid}", 0.12),
    ("POST", "/api/v1/payments/{id}/capture", 0.05),
    ("GET", "/api/v1/inventory/{pid}", 0.05),
]
QUERIES = {
    "/api/v1/orders/{id}": "SELECT id, customer_id, status, total_cents, created_at FROM orders WHERE id = $1",
    "/api/v1/orders?customer_id={cid}&limit=50": "SELECT id, status, total_cents FROM orders WHERE customer_id = $1 ORDER BY created_at DESC LIMIT 50",
    "/api/v1/orders": "INSERT INTO orders (customer_id, status, total_cents) VALUES ($1, $2, $3) RETURNING id",
    "/api/v1/customers/{cid}": "SELECT id, email, tier, created_at FROM customers WHERE id = $1",
    "/api/v1/orders/{id}/status": "UPDATE orders SET status = $2, updated_at = now() WHERE id = $1",
    "/api/v1/products/{pid}": "SELECT id, sku, name, price_cents FROM products WHERE id = $1",
    "/api/v1/payments/{id}/capture": "SELECT id, status, amount_cents FROM payments WHERE order_id = $1 FOR UPDATE",
    "/api/v1/inventory/{pid}": "SELECT sku, on_hand, reserved FROM inventory WHERE product_id = $1",
}
UAS = ["okhttp/4.12.0", "python-requests/2.32.3", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)",
       "orders-web/3.8.1", "Go-http-client/2.0"]

TRACE_TIMEOUT = [
    "Traceback (most recent call last):",
    '  File "/app/orders/api/handlers.py", line 212, in get_order',
    "    async with db.session() as s:",
    '  File "/app/orders/db/session.py", line 58, in session',
    "    conn = await self._pool.acquire(timeout=self._acquire_timeout)",
    '  File "/usr/local/lib/python3.12/site-packages/sqlalchemy/pool/impl.py", line 168, in _do_get',
    "    raise exc.TimeoutError(",
    "sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 0 reached, "
    "connection timed out, timeout 5.00 (Background on this error at: https://sqlalche.me/e/20/3o7r)",
]
TRACE_GATEWAY = [
    "Traceback (most recent call last):",
    '  File "/app/orders/payments/capture.py", line 94, in capture',
    "    resp = await self._client.post(url, json=body, timeout=8.0)",
    '  File "/usr/local/lib/python3.12/site-packages/httpx/_client.py", line 1905, in post',
    "    return await self.request(",
    "httpx.ReadTimeout: timed out after 8.0s waiting for payments-gw.internal:443",
]


class LogBuilder:
    def __init__(self, seed: int, start: datetime):
        self.rng = random.Random(seed)
        self.t = start
        self.lines: list[str] = []
        self.needles: dict[str, list[str]] = {
            "error_lines": [], "warn_lines": [], "traceback_frames": [],
            "status_5xx_lines": [], "incident_onset": [], "root_cause": [],
            "breaker_transitions": [], "rollback": [], "shutdown_summary": [],
        }
        self.requests = 0
        self.errors = 0

    def _ts(self) -> str:
        self.t += timedelta(milliseconds=self.rng.randint(40, 520))
        return self.t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{self.t.microsecond // 1000:03d}Z"

    def emit(self, level: str, component: str, msg: str, *needles: str) -> str:
        line = f"{self._ts()} {level:<5} {component:<9} {msg}"
        self.lines.append(line)
        if level in ("ERROR", "FATAL"):
            self.needles["error_lines"].append(line)
        if level == "WARN":
            self.needles["warn_lines"].append(line)
        for n in needles:
            self.needles[n].append(line)
        return line

    def raw(self, text: str, *needles: str) -> None:
        self.lines.append(text)
        for n in needles:
            self.needles[n].append(text)

    def rid(self) -> str:
        return f"{self.rng.getrandbits(48):012x}"

    def request(self, status: int | None = None, slow: bool = False) -> None:
        rng = self.rng
        method, tmpl, _ = rng.choices(PATHS, weights=[p[2] for p in PATHS])[0]
        path = tmpl.format(id=rng.randint(10_000, 99_999), cid=rng.randint(1_000, 9_999),
                           pid=rng.randint(100, 2_400))
        rid = self.rid()
        if rng.random() < 0.35:
            key = rng.choice(["customer:{n}:profile", "product:{n}", "order:{n}:summary"]).format(
                n=rng.randint(100, 9_999))
            self.emit("DEBUG", "cache", f"req_id={rid} key={key} hit={'true' if rng.random() < 0.82 else 'false'} "
                                        f"ttl_s={rng.randint(5, 300)}")
        if status is None or status < 500:
            self.emit("DEBUG", "db", f'req_id={rid} query="{QUERIES[tmpl]}" rows={rng.randint(0, 50)} '
                                     f"dur_ms={rng.uniform(0.6, 9.0):.1f}")
        if status is None:
            status = rng.choices([200, 201, 204, 304, 400, 401, 404, 409, 429],
                                 weights=[70, 8, 4, 3, 3, 2, 6, 1, 1])[0]
        dur = rng.randint(900, 2600) if slow else int(rng.lognormvariate(3.3, 0.6))
        if slow:
            self.emit("WARN", "http", f"req_id={rid} slow request path={path} dur_ms={dur} threshold_ms=750")
        self.requests += 1
        line = self.emit("INFO", "http", f"req_id={rid} method={method} path={path} status={status} dur_ms={dur} "
                                         f"bytes={rng.randint(120, 9_000)} ua=\"{rng.choice(UAS)}\" "
                                         f"user=u_{rng.randint(1_000, 9_999)}")
        if status >= 500:
            self.errors += 1
            self.needles["status_5xx_lines"].append(line)

    def metrics(self, active: int, waiting: int, err_field: bool) -> None:
        rng = self.rng
        extra = f" errors={rng.choice([0, 0, 0, 1])}" if err_field else ""
        self.emit("INFO", "metrics", f"pool.active={active} pool.idle={max(0, 50 - active)} pool.waiting={waiting} "
                                     f"rps={rng.uniform(24, 38):.1f} p50_ms={rng.randint(18, 30)} "
                                     f"p99_ms={rng.randint(90, 160)} heap_mb={rng.randint(380, 460)}{extra}")

    def gateway_failure(self) -> None:
        rid = self.rid()
        self.emit("ERROR", "payments", f"req_id={rid} capture failed for order {self.rng.randint(10_000, 99_999)}, "
                                       "upstream payments-gw timed out")
        for fr in TRACE_GATEWAY:
            self.raw(fr, "traceback_frames")
        self.request(status=502)


def build_log(*, lines: int = 5000, seed: int = 20261011, variant: str = "incident") -> LogBuilder:
    b = LogBuilder(seed, datetime(2026, 10, 11, 14, 0, 0, tzinfo=timezone.utc))
    rng = b.rng
    err_field = variant == "errfield"
    incident = variant in ("incident", "errfield")

    b.emit("INFO", "main", "starting orders-api version=2.14.3 commit=9c1e4a7 python=3.12.6 pid=1")
    b.emit("INFO", "config", "loaded config source=configmap/orders-api-cfg rev=417 db.pool.max_size=50 "
                             "db.pool.acquire_timeout_s=5")
    b.emit("INFO", "db", "connected to postgres host=orders-db-primary.internal:5432 db=orders pool_size=50")
    b.emit("INFO", "cache", "connected to redis host=orders-cache.internal:6379 db=0")
    b.emit("INFO", "main", "listening on 0.0.0.0:8080 workers=4")

    # Where the story beats land, as fractions of the log so --lines scales them.
    cause_at, onset_at = int(lines * 0.59), int(lines * 0.63)
    rollback_at, end_at = int(lines * 0.71), lines - 6
    breaker_open = False
    since_health = since_metrics = 0

    while len(b.lines) < end_at:
        n = len(b.lines)
        since_health += 1
        since_metrics += 1
        if since_health >= 30:
            since_health = 0
            b.emit("DEBUG", "http", "req_id=- method=GET path=/healthz status=200 dur_ms=1 ua=\"kube-probe/1.31\"")
            continue

        in_incident = incident and onset_at <= n < rollback_at
        degrading = incident and cause_at <= n < onset_at

        if since_metrics >= 45:
            since_metrics = 0
            if in_incident:
                b.metrics(20, rng.randint(30, 90), err_field)
            elif degrading:
                b.metrics(20, rng.randint(0, 12), err_field)
            else:
                b.metrics(rng.randint(8, 22), 0, err_field)
            continue

        if incident and n >= cause_at and not b.needles["root_cause"]:
            # The cause is an INFO line with no error vocabulary in it. That is
            # what config regressions look like in practice.
            b.emit("INFO", "config", "reloaded config source=configmap/orders-api-cfg rev=418 "
                                     "changed: db.pool.max_size 50 -> 20", "root_cause")
            continue

        if degrading and rng.random() < 0.06:
            b.emit("WARN", "db", f"pool acquire slow wait_ms={rng.randint(300, 2400)} "
                                 f"pool.active=20/20 pool.waiting={rng.randint(1, 14)}")
            continue

        if in_incident:
            roll = rng.random()
            if not b.needles["incident_onset"]:
                rid = b.rid()
                b.emit("ERROR", "db", f"req_id={rid} could not acquire connection: QueuePool limit of size 20 "
                                      "overflow 0 reached, connection timed out, timeout 5.00", "incident_onset")
                for fr in TRACE_TIMEOUT:
                    b.raw(fr, "traceback_frames")
                b.request(status=503)
                continue
            if not breaker_open and n >= onset_at + 140:
                breaker_open = True
                b.emit("WARN", "breaker", "circuit db-primary state=OPEN failures=50/60s cooldown_s=30",
                       "breaker_transitions")
                continue
            if roll < 0.22:
                rid = b.rid()
                b.emit("ERROR", "db", f"req_id={rid} could not acquire connection: QueuePool limit of size 20 "
                                      "overflow 0 reached, connection timed out, timeout 5.00")
                if rng.random() < 0.08:
                    for fr in TRACE_TIMEOUT:
                        b.raw(fr, "traceback_frames")
                b.request(status=503)
                continue
            if roll < 0.30:
                b.emit("WARN", "http", f"req_id={b.rid()} retrying upstream call attempt={rng.randint(2, 3)}/3 "
                                       f"backoff_ms={rng.choice([200, 400, 800])}")
                continue
            if roll < 0.36 and breaker_open:
                b.emit("WARN", "breaker", f"req_id={b.rid()} short-circuited call to db-primary state=OPEN")
                b.request(status=503)
                continue
            b.request(slow=rng.random() < 0.15)
            continue

        if incident and n >= rollback_at and not b.needles["rollback"]:
            b.emit("INFO", "config", "reloaded config source=configmap/orders-api-cfg rev=419 "
                                     "changed: db.pool.max_size 20 -> 50", "rollback")
            b.emit("INFO", "breaker", "circuit db-primary state=CLOSED after 4 successful probes",
                   "breaker_transitions")
            continue

        roll = rng.random()
        if roll < 0.0025:
            b.gateway_failure()
        elif roll < 0.0075:
            b.request(slow=True)
        else:
            b.request()

    b.emit("INFO", "main", "received SIGTERM, draining in-flight requests (12 active)")
    b.emit("INFO", "http", "drained, closing listener")
    b.emit("INFO", "db", "closing pool connections=50")
    b.emit("INFO", "main", f"shutdown complete requests_served={b.requests} responses_5xx={b.errors} "
                           "uptime_s=1734", "shutdown_summary")
    return b


#: Deliberately names nothing that appears inside the log. An earlier
#: `cat /var/log/orders-api/server.log` matched the config lines' own
#: `configmap/orders-api-cfg` and "recovered" the root cause for every prompt,
#: which measured the fixture's naming, not prompt-awareness.
HOOK_COMMAND = "tail -n 5000 /var/log/app/current.log"


def _hook_path(text: str, prompt: str | None = None) -> tuple[str, bool]:
    """Run the text through the exact PostToolUse hook an agent's Bash call hits.

    With ``prompt``, a one-line transcript holding it is written first, so the
    hook reads the user's question the way it does in a real session.
    """
    import tempfile

    from memor.posttool_compress import build_response

    req = {"tool_name": "Bash", "tool_input": {"command": HOOK_COMMAND},
           "tool_response": {"stdout": text, "stderr": "", "interrupted": False, "isImage": False}}
    with tempfile.TemporaryDirectory() as tmp:
        if prompt:
            transcript = f"{tmp}/session.jsonl"
            with open(transcript, "w") as fh:
                fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}))
            req["transcript_path"] = transcript
        out = build_response(req)
    if not out:
        return text, False
    return out["hookSpecificOutput"]["updatedToolOutput"]["stdout"], True


_ID = re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{8,}\b|\d+")


def _fact(line: str) -> str:
    """A needle with its ids, numbers and timestamp blanked.

    Written here rather than borrowed from the compressor, so the scorer does
    not share the code it is scoring. Fifty pool timeouts that differ only in
    request id are one fact; a compressor that keeps one of them and says how
    many there were has not lost the other forty-nine.
    """
    return _ID.sub("#", line)


def retention(builder: LogBuilder, compressed: str) -> dict[str, tuple[int, int, int, int]]:
    """Per needle category: (lines kept, lines total, distinct kept, distinct total)."""
    out_lines = compressed.split("\n")
    kept = set(out_lines)
    kept_facts = {_fact(ln) for ln in out_lines}
    out = {}
    for k, v in builder.needles.items():
        if not v:
            continue
        facts = {_fact(ln) for ln in v}
        out[k] = (sum(1 for ln in v if ln in kept), len(v),
                  sum(1 for f in facts if f in kept_facts), len(facts))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lines", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20261011)
    ap.add_argument("--variant", choices=["incident", "quiet", "errfield"], default="incident")
    ap.add_argument("--write", metavar="PATH", help="save the generated log here")
    ap.add_argument("--write-compressed", metavar="PATH", help="save memor's output here")
    ap.add_argument("--prompt", help="the user's question, as the hook would read it "
                                      "from the transcript; retention is then scored "
                                      "on the hook's output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from memor.compress import compress_text
    from memor.compress.detect import detect_content_type

    b = build_log(lines=args.lines, seed=args.seed, variant=args.variant)
    text = "\n".join(b.lines)
    if args.write:
        with open(args.write, "w") as fh:
            fh.write(text + "\n")

    r = compress_text(text)
    r_file = compress_text(text, file_path="server.log")
    hook_text, hook_fired = _hook_path(text, args.prompt)
    if args.write_compressed:
        with open(args.write_compressed, "w") as fh:
            fh.write(r.text + "\n")

    from memor.tokencount import count_tokens

    hook_after = count_tokens(hook_text)
    ret = retention(b, hook_text if args.prompt else r.text)
    summary = {
        "prompt": args.prompt,
        "variant": args.variant, "lines": len(b.lines), "chars": len(text),
        "detected_as": detect_content_type(text),
        "tokens_before": r.tokens_before, "tokens_after": r.tokens_after,
        "saved_pct": round(100 * (1 - r.tokens_after / r.tokens_before), 1),
        "lines_after": r.text.count("\n") + 1,
        "as_file_read_saved_pct": round(100 * (1 - r_file.tokens_after / r_file.tokens_before), 1),
        "hook_fired": hook_fired,
        "hook_saved_pct": round(100 * (1 - hook_after / r.tokens_before), 1),
        "retention": {k: {"kept": a, "total": t, "distinct_kept": da, "distinct_total": dt}
                      for k, (a, t, da, dt) in ret.items()},
    }
    if args.json:
        print(json.dumps(summary, indent=2))
        return

    print(f"variant={summary['variant']}  lines={summary['lines']}  chars={summary['chars']:,}  "
          f"detected_as={summary['detected_as']}")
    print(f"tokens (cl100k): {r.tokens_before:,} -> {r.tokens_after:,}  "
          f"saved {summary['saved_pct']}%  ({summary['lines_after']} lines kept)")
    print(f"  as a .log file read:    saved {summary['as_file_read_saved_pct']}%")
    print(f"  via PostToolUse hook:   saved {summary['hook_saved_pct']}%  (fired={hook_fired})")
    scored_on = f"hook output, prompt: {args.prompt!r}" if args.prompt else "no prompt"
    print(f"retention of answer-critical lines   (distinct = ids and numbers aside; {scored_on})")
    for k, (a, t, da, dt) in ret.items():
        mark = "ok " if da == dt else "LOST"
        print(f"  {mark} {k:<20} distinct {da}/{dt}   lines {a}/{t}")


if __name__ == "__main__":
    main()
