#!/usr/bin/env python3
"""pipeline-health-sweep.py — automated pipeline health sweep.

Runs the five pipeline-health checks — the three DB queries from the
`pipeline-health-check` procedure card (blocked plans, plan-status drift,
flagged changes) plus the projection-vs-replay drift scan via conduit-srv
`GET /wr/drift-scan` (plan 1285) and the stale role-lease check via
nebula-srv `GET /api/role-leases/stale` (plan 1286) — and posts findings to the Assembly
`drift-reports` forum, replacing the manual turn-start checks.

Canonical scheduling: systemd user timer `nexus-pipeline-health.timer`
(every 30 min). State (last-posted signature) lives in a local cache file so
repeat runs with unchanged findings do not spam the forum; a new thread is
posted when the finding set changes, and a short resolution thread when it
clears.

Usage:
  pipeline-health-sweep.py                 # run + post (only when findings change)
  pipeline-health-sweep.py --dry-run       # print report, never post/update state
  pipeline-health-sweep.py --force         # post even if signature unchanged
  pipeline-health-sweep.py --quiet         # suppress report output (cron mode)

Options:
  --forum SLUG       Assembly forum to post to (default: drift-reports)
  --role NAME        Role attribution (default: inspector)
  --model ID         Model attribution (default: automated/nexus-pipeline-health-sweep)
  --assembly-url URL Assembly API base (default: http://localhost:3107)
  --state PATH       State file (default: ~/.cache/nexus-pipeline-health-state.json)
  --nebula-url URL  nebula-srv base for the role-lease stale check
                     (default: http://localhost:3101)
  --dry-run          Print report only; do not post, do not touch state
  --force            Post even when the finding signature is unchanged
  --quiet            Suppress the report on stdout
  -h, --help         Show this help

Exit codes: 0 ok (findings may exist), 1 error (DB/API failure).
"""

import argparse
import hashlib
import json
import os
import sys

import psycopg2

# ── config ────────────────────────────────────────────────────────────
DEFAULT_ASSEMBLY_URL = "http://localhost:3107"
DEFAULT_ROLE = "inspector"
DEFAULT_MODEL = "automated/nexus-pipeline-health-sweep"
DEFAULT_FORUM = "drift-reports"
DEFAULT_STATE = os.path.expanduser("~/.cache/nexus-pipeline-health-state.json")
DEFAULT_CONDUIT_URL = "http://localhost:3104"
DEFAULT_NEBULA_URL = "http://localhost:3101"

DB = dict(
    host=os.environ.get("PGHOST", "localhost"),
    port=int(os.environ.get("PGPORT", "5432")),
    user=os.environ.get("PGUSER", "pguser"),
    password=os.environ.get("PGPASSWORD", "pgpass"),
    dbname=os.environ.get("PGDATABASE", "nexus"),
)

# ── the three health checks (mirrors pipeline-health-check card v2) ────
#
# Receipt surface. Receipts left `vision.receipts` at the 2026-09-13
# write-boundary cutover: that table's newest row is 2026-09-13 21:19Z and it
# has taken nothing since, while every plan touched in the last 30 days has
# receipts only in the unified projection. Reading the retired table made
# checks 1 and 2 return an empty "latest" set for every newer plan — the
# sweep reported "None" while conduit (:3100) correctly listed plan 8261650
# as BLOCKED on a builder receipt that existed only in the projection.
# `nebula.receipts_unified` is the live projection conduit's own
# get_plan_receipts serves, and it is a strict superset of the retired table
# (zero plans present in vision.receipts are missing from it).
RECEIPT_SURFACE = "nebula.receipts_unified"

QUERY_BLOCKED = f"""
WITH latest AS (
  SELECT DISTINCT ON (plan_id) plan_id, type, created_at
  FROM {RECEIPT_SURFACE} WHERE plan_id ~ '^[0-9]+$'
  ORDER BY plan_id, created_at DESC)
SELECT plan_id, type, to_char(created_at, 'YYYY-MM-DD') AS since
FROM latest WHERE type IN ('BLOCK','HOLD') ORDER BY created_at
"""

# Drift v3 (to-do 299fce45): adds the explicit report-only FLAG — a plan is
# flagged when ALL THREE hold: latest receipt PLAN_CREATE >24h AND a builder
# ticket unclaimed >24h AND external completion evidence exists
# (IMPLEMENTATION/REVIEW_PASS receipts OR inspection/record evidence).
QUERY_DRIFT = f"""
WITH latest AS (
  SELECT DISTINCT ON (plan_id) plan_id, type, created_at
  FROM {RECEIPT_SURFACE} WHERE plan_id ~ '^[0-9]+$'
  ORDER BY plan_id, created_at DESC),
terminal AS (
  -- Plans already closed in the canonical plan row cannot be "stuck pending";
  -- their receipt chain merely stops at PLAN_CREATE (drift in the chain, not
  -- in the lifecycle). Without this, six archived plans (0003, 0006, 0008,
  -- 0015, 1290, 8261652) were reported as implemented-but-pending on the
  -- unified surface. Plans with no plan row are still reported: unknown is
  -- not the same as closed.
  SELECT plan_number::text AS plan_id
  FROM nebula.implementation_plans
  WHERE status IN ('completed','archived','closed')),
stuck AS (
  SELECT plan_id, created_at FROM latest
  WHERE type = 'PLAN_CREATE' AND created_at < NOW() - INTERVAL '24 hours'
    AND plan_id NOT IN (SELECT plan_id FROM terminal)),
ticks AS (
  SELECT plan_id,
    count(*) FILTER (WHERE role = 'builder' AND status = 'open'
                     AND claimed_at IS NULL
                     AND created_at < NOW() - INTERVAL '24 hours') AS unclaimed_24h,
    count(*) FILTER (WHERE status = 'expired'
      OR (status IN ('open','claimed','stale')
          AND expires_at IS NOT NULL AND expires_at < NOW())) AS expired,
    count(*) FILTER (WHERE status = 'cancelled') AS cancelled
  FROM vision.tickets GROUP BY plan_id),
receipt_ev AS (
  SELECT plan_id, count(*) AS n
  FROM {RECEIPT_SURFACE}
  WHERE type IN ('IMPLEMENTATION','REVIEW_PASS') AND plan_id ~ '^[0-9]+$'
  GROUP BY plan_id),
rec_ev AS (
  SELECT s.plan_id,
    (SELECT count(*) FROM nebula.agent_records ar
      WHERE (ar.plan_ref = s.plan_id
         OR ar.content ~* ('(^|[^0-9])' || s.plan_id || '([^0-9]|$)'))
        AND ar.record_type IN ('report','inspection','engineering_log','assessment','analysis','decision')
        AND COALESCE(ar.title,'') NOT ILIKE '%pre-fk-snapshot%'
        AND COALESCE(ar.title,'') NOT ILIKE '%drift%'
        AND COALESCE(ar.title,'') NOT ILIKE '%ghost%'
        AND COALESCE(ar.title,'') NOT ILIKE '%cross-reference%'
        AND COALESCE(ar.title,'') NOT ILIKE 'CROSS REFERENCES%') AS n
  FROM stuck s)
SELECT s.plan_id, to_char(s.created_at,'YYYY-MM-DD') AS last_plan_create,
  COALESCE(t.expired,0) AS expired_tickets,
  COALESCE(t.cancelled,0) AS cancelled_tickets,
  COALESCE(ev.n,0) + COALESCE(re.n,0) AS evidence_rows,
  COALESCE(t.unclaimed_24h,0) AS unclaimed_24h,
  ((COALESCE(t.unclaimed_24h,0) > 0 OR COALESCE(t.expired,0) > 0)
   AND (COALESCE(ev.n,0) + COALESCE(re.n,0)) > 0) AS drift_flag
FROM stuck s
LEFT JOIN ticks t ON t.plan_id = s.plan_id
LEFT JOIN receipt_ev ev ON ev.plan_id = s.plan_id
LEFT JOIN rec_ev re ON re.plan_id = s.plan_id
ORDER BY s.created_at
"""

QUERY_FLAGGED = """
SELECT record_type, role, left(title,70) AS title, to_char(created_at,'YYYY-MM-DD HH24:MI') AS created
FROM nebula.agent_records
WHERE ((tags && ARRAY['type:rejection','type:violation','type:incident'])
    OR record_type = 'inspection')
  AND NOT (tags && ARRAY['status:resolved','status:done','status:closed','resolved','done','closed'])
  AND NOT (tags && ARRAY['cycle:hourly-maintenance','hourly-maintenance'])
  AND NOT (record_type = 'inspection' AND (title IN ('.gitkeep','REGISTRY') OR tags = '{}'))
ORDER BY created_at DESC LIMIT 20
"""

QUERY_WR_ASSETS = """
-- T01 invariant (V092 guard): every live vision WorkRequest must carry a
-- canonical asset (asset:nexus:vision_work_requests:<uuid>, kind work_request).
-- Regression: V089 was a one-shot backfill; 2 settled WRs slipped through
-- before trg_vision_work_requests_asset landed (maint-2026-08-09-1733,
-- wr-conf-001). A row here means the trigger did NOT fire (row pre-dates V092,
-- or an explicit-asset path bypassed it) — flag for backfill, never auto-fix.
SELECT wr_id, work_request_uuid, status, to_char(recorded_on_dt,'YYYY-MM-DD HH24:MI') AS recorded
FROM vision.work_requests
WHERE asset_id IS NULL
ORDER BY recorded_on_dt
"""

QUERY_WR_ENTITY_KEY = """
-- Q4 P1 invariant (V093 backfill): every live vision WorkRequest with a
-- compile-unit identity (non-empty wr_id) must carry a CCNF content identity
-- (entity_key) — the pure-Python/Go/Rust-identical hash of the canonical WR
-- shape (system execute on workrequest:<wr_id>), derived at birth by
-- vision_bridge / vision-srv / losm-host write paths. Regression: migration
-- v37 added the column but no write path populated it (all 24 rows were
-- NULL) until V093. A row here means the write path did NOT stamp an
-- entity_key (pre-dates V093, or a bypass path like the runtime kernel's
-- explicit-asset insert) — flag for backfill, never auto-fix. Identity-less
-- rows (wr_id NULL/'') are resolution-comparator fixtures (e.g. the
-- `wr-mongo-wiring` seed) with no compile-unit identity, exactly the class
-- conduit's v17 event-log backfill skips — they are not WRs and cannot
-- carry a birth identity, so they are out of scope here.
SELECT wr_id, work_request_uuid, status, to_char(recorded_on_dt,'YYYY-MM-DD HH24:MI') AS recorded
FROM vision.work_requests
WHERE entity_key IS NULL
  AND wr_id IS NOT NULL
  AND wr_id <> ''
ORDER BY recorded_on_dt
"""


def run_checks(cur):
    """Return dict of DB findings: blocked/drift/flagged rows.
    Projection drift is fetched separately (HTTP) — see fetch_projection_drift."""
    cur.execute(QUERY_BLOCKED)
    blocked = cur.fetchall()
    cur.execute(QUERY_DRIFT)
    drift = cur.fetchall()
    cur.execute(QUERY_FLAGGED)
    flagged = cur.fetchall()
    cur.execute(QUERY_WR_ASSETS)
    wr_assets = cur.fetchall()
    cur.execute(QUERY_WR_ENTITY_KEY)
    wr_entity_key = cur.fetchall()
    return {"blocked": blocked, "drift": drift, "flagged": flagged,
            "wr_assets": wr_assets, "wr_entity_key": wr_entity_key}


def emit_drift_findings(conn, drift_rows, nebula_url, role, model, dry_run=False):
    """Emit `type:finding` agent records (to:architect + to:watchdog) for
    flagged drift plans — the report-only routing layer of the to-do 299fce45
    flag. Dedup: an existing open finding for the same plan_id suppresses a
    re-emit, so the 30-min timer cannot spam the inbox.

    Returns (emitted, skipped) counts.
    """
    import urllib.request

    emitted, skipped = 0, 0
    # Commit any prior work and take a FRESH snapshot of plan_ids that already
    # have an OPEN type:finding record — a stale transaction snapshot would
    # miss records committed by earlier runs and cause duplicate emits.
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT plan_ref FROM nebula.agent_records
           WHERE plan_ref IS NOT NULL
             AND tags && ARRAY['type:finding']
             AND NOT (tags && ARRAY['status:closed','status:resolved','status:done'])"""
    )
    already_open = {r[0] for r in cur.fetchall()}
    cur.close()

    for row in drift_rows:
        pid = row[0]
        if not row[6]:  # drift_flag
            continue
        if pid in already_open:
            skipped += 1
            continue
        already_open.add(pid)  # same run may flag multiple, keep set local
        title = f"Drift finding: plan {pid} stuck-pending with external completion evidence"
        content = (
            f"**Report-only flag (never auto-closed).** Plan `{pid}` is stuck-pending "
            f"(latest receipt PLAN_CREATE >24h, last {row[1]}) with a builder ticket "
            f"expired ({row[2]}) / unclaimed >24h ({row[5]}) and external completion "
            f"evidence ({row[4]} IMPLEMENTATION/REVIEW_PASS receipt or agent-record rows).\n\n"
            f"Suggested close: IMPLEMENTATION + REVIEW_PASS receipts, or CANCELLED receipt "
            f"if abandoned — confirm manually before acting. Source: `pipeline-health-sweep.py` "
            f"drift check v3 (to-do 299fce45)."
        )
        payload = {
            "recordType": "analysis",
            "role": role,
            "title": title,
            "content": content,
            "tags": ["to:architect", "to:watchdog", "type:finding", "status:open"],
            "planRef": pid,
            "level": 3,
            "visibilityScope": "all",
        }
        if dry_run:
            print(f"[dry-run] would emit finding record for plan {pid} (planRef={pid})")
            emitted += 1
            continue
        try:
            req = urllib.request.Request(
                f"{nebula_url}/api/agent-records",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                json.loads(resp.read())
            emitted += 1
        except Exception as e:  # noqa: BLE001
            print(f"WARN: finding record emit for plan {pid} failed: {e}", file=sys.stderr)
    return emitted, skipped


def fetch_projection_drift(conduit_url):
    """Query conduit-srv `GET /wr/drift-scan` (plan 1285) for projection-vs-replay
    drift across active work requests.

    Returns the findings list, or None when conduit-srv is unreachable so the
    sweep still reports the DB checks (and does not fail the run).
    """
    import urllib.request
    try:
        with urllib.request.urlopen(
            f"{conduit_url}/wr/drift-scan?limit=200", timeout=10
        ) as resp:
            data = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        print(
            f"WARN: projection-drift scan unavailable (conduit-srv {conduit_url}): {e}",
            file=sys.stderr,
        )
        return None
    if not data.get("ok"):
        print(f"WARN: drift-scan returned ok=false: {data}", file=sys.stderr)
        return None
    return data.get("findings", [])


def fetch_role_lease_stale(nebula_url):
    """Query nebula-srv `GET /api/role-leases/stale` (plan 1286) for ACTIVE
    role leases whose window has expired or budget is exhausted — work is
    waiting but no session is consuming it.

    Returns the items list, or None when nebula-srv is unreachable.
    """
    import urllib.request
    try:
        with urllib.request.urlopen(
            f"{nebula_url}/api/role-leases/stale", timeout=10
        ) as resp:
            data = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        print(
            f"WARN: role-lease stale check unavailable (nebula-srv {nebula_url}): {e}",
            file=sys.stderr,
        )
        return None
    return data.get("items", [])


def signature(findings):
    """Stable signature of the finding set (plan/record ids) for dedup."""
    items = []
    for row in findings["blocked"]:
        items.append(f"blocked|{row[0]}")
    # include the signal columns so evidence/ticket-state changes re-post
    for row in findings["drift"]:
        items.append(f"drift|{row[0]}|{row[2]}|{row[3]}|{row[4]}|{row[5]}|{row[6]}")
    for row in findings["flagged"]:
        items.append(f"flagged|{row[2]}|{row[3]}")  # title|created
    # canonical-asset findings (V092 guard — stable set, but keep them so a
    # row clearing re-posts a resolution)
    for row in findings["wr_assets"]:
        items.append(f"wr_asset|{row[0]}|{row[1]}")  # wr_id|work_request_uuid
    # entity_key findings (Q4 P1 / V093 — same discipline so fixes re-post)
    for row in findings["wr_entity_key"]:
        items.append(f"wr_entity_key|{row[0]}|{row[1]}")  # wr_id|work_request_uuid
    # projection drift (work_request_uuid + state signals so fixes re-post)
    for f in findings.get("projection") or []:
        items.append(
            f"projection|{f.get('work_request_uuid')}|{f.get('status')}|"
            f"{f.get('expected_state')}|{f.get('live_state')}|"
            f"{f.get('expected_vision_ir_version')}|{f.get('live_vision_ir_version')}"
        )
    # stale role leases (plan 1286)
    for item in findings.get("role_leases") or []:
        items.append(
            f"role_lease|{item.get('id')}|{item.get('role')}|"
            f"{item.get('consumed_units')}|{item.get('budget_units') or '-'}"
        )
    items.sort()
    return hashlib.sha256("\n".join(items).encode()).hexdigest()[:16]


def build_report(findings, role, model):
    """Markdown report body."""
    blocked, drift, flagged = findings["blocked"], findings["drift"], findings["flagged"]
    projection = findings.get("projection") or []
    proj_unavailable = findings.get("projection") is None
    lines = [
        "# Pipeline Health Sweep (automated)",
        "",
        f"**Role:** {role} ({model})  \n"
        f"**Source:** `nexus/bin/pipeline-health-sweep.py` — systemd timer `nexus-pipeline-health.timer` (every 30 min).",
        "",
        "DB checks mirror the `pipeline-health-check` procedure card (drift v2: cancelled-ticket signal, noise-excluded evidence); projection drift comes from conduit-srv `GET /wr/drift-scan` (plan 1285).",
        "",
    ]
    lines.append("## 1. Blocked plans (latest receipt BLOCK/HOLD)")
    if blocked:
        lines += [
            "",
            "| plan_id | type | since |",
            "|---|---|---|",
        ]
        for pid, typ, since in blocked:
            lines.append(f"| {pid} | {typ} | {since} |")
        lines.append("")
        lines.append(f"**{len(blocked)} blocked.** NOTE: older BLOCK/HOLD receipts may be stale markers on already-cancelled/archived ghost plans — confirm before dispatching.")
    else:
        lines.append("\nNone.")
    lines.append("")

    lines.append("## 2. Plan-status drift (stuck pending + expired/unclaimed ticket + external evidence)")
    if drift:
        lines += [
            "",
            "| plan_id | last_plan_create | expired | unclaimed>24h | cancelled | evidence | FLAG |",
            "|---|---|---|---|---|---|---|",
        ]
        # NOTE: do not reuse the outer `flagged` name here. `flagged` is the
        # §3 flagged-changes result and is consumed below as 4-tuples;
        # shadowing it with these 5-tuples crashed build_report with
        # "too many values to unpack" whenever a drift row carried the FLAG —
        # which is exactly when a report is most needed.
        drift_flagged = []
        for pid, since, exp, canc, ev, unclaimed, flag in drift:
            mark = "🚩" if flag else ""
            lines.append(f"| {pid} | {since} | {exp} | {unclaimed} | {canc} | {ev} | {mark} |")
            if flag:
                drift_flagged.append((pid, since, exp, unclaimed, ev))
        lines.append("")
        lines.append(
            f"**{len(drift_flagged)} flagged** (🚩 = stuck PLAN_CREATE >24h AND expired/unclaimed builder ticket AND external completion evidence — implemented-but-pending, report-only, never auto-closed; finding records tagged `to:architect`+`to:watchdog` are emitted for these)."
        )
        lines.append("")
        lines.append("**Remediation:** evidence>0 → implemented-but-pending: close via IMPLEMENTATION + REVIEW_PASS. expired/unclaimed>0 with no evidence → abandoned: close via CANCELLED receipt (`POST /api/receipts/`), or re-arm ticket if genuinely wanted. Heuristic — confirm each manually.")
    else:
        lines.append("\nNone — no plan has been stuck-pending (PLAN_CREATE) >24h.")
    lines.append("")

    lines.append("## 3. Flagged changes / blocker reports")
    if flagged:
        lines += [
            "",
            "| record_type | role | title | created |",
            "|---|---|---|---|",
        ]
        for rtype, role_, title, created in flagged:
            lines.append(f"| {rtype} | {role_} | {title} | {created} |")
        lines.append("")
        lines.append("**Latest 20** (older flagged records exist; tail the query for more).")
    else:
        lines.append("\nNone.")
    lines.append("")

    lines.append("## 3b. WorkRequest canonical-asset invariant (T01 / V092 guard)")
    wr_assets = findings.get("wr_assets") or []
    if wr_assets:
        lines += [
            "",
            "| wr_id | work_request_uuid | status | recorded |",
            "|---|---|---|---|",
        ]
        for wr_id, wr_uuid, status, recorded in wr_assets:
            lines.append(f"| {wr_id} | {wr_uuid} | {status} | {recorded} |")
        lines.append("")
        lines.append(
            f"**{len(wr_assets)} WRs missing canonical assets.** The `trg_vision_work_requests_asset` "
            "BEFORE INSERT trigger (V092) auto-registers assets for new WRs; rows here pre-date V092 or "
            "bypassed it via an explicit-asset path. **Remediation:** re-run "
            "`nexus/sql/V089__vision_work_requests_asset_id_backfill.sql` (idempotent) or call "
            "`semantics.ensure_registered_work_request_asset('<uuid>')` — report-only, never auto-fix."
        )
    else:
        lines.append(
            "\nNone — every live `vision.work_requests` row carries a canonical asset "
            "(guard: `trg_vision_work_requests_asset`, V092)."
        )
    lines.append("")

    lines.append("## 3c. WorkRequest entity_key invariant (Q4 P1 / V093 guard)")
    wr_ek = findings.get("wr_entity_key") or []
    if wr_ek:
        lines += [
            "",
            "| wr_id | work_request_uuid | status | recorded |",
            "|---|---|---|---|",
        ]
        for wr_id, wr_uuid, status, recorded in wr_ek:
            lines.append(f"| {wr_id} | {wr_uuid} | {status} | {recorded} |")
        lines.append("")
        lines.append(
            f"**{len(wr_ek)} WRs missing entity_key.** The CCNF content identity "
            "(SHA256 over the sorted {{domain,intent,actor,scope}} signature of the "
            "canonical WR shape) is derived at birth by the vision_bridge / vision-srv / "
            "losm-host write paths; `vision.work_requests` rows here pre-date V093 or "
            "bypassed those paths. **Remediation:** re-run "
            "`nexus/sql/V093__vision_work_requests_entity_key_backfill.sql` (idempotent) "
            "or wire the creating caller to derive `entity_key` via "
            "`nexus_core.wrp.identity` — report-only, never auto-fix."
        )
    else:
        lines.append(
            "\nNone — every live `vision.work_requests` row carries a CCNF content "
            "identity (entity_key; backfill: V093)."
        )
    lines.append("")

    lines.append("## 4. Projection drift (event replay vs live work_request_state)")
    if proj_unavailable:
        lines.append("\n**Scan unavailable** — conduit-srv `/wr/drift-scan` unreachable on this run; projection check skipped (the 3 DB checks above are still reported).")
    elif projection:
        lines += [
            "",
            "| wr_id | status | expected_state | live_state | ir_exp | ir_live |",
            "|---|---|---|---|---|---|",
        ]
        for f in projection:
            lines.append(
                f"| {f.get('wr_id') or '-'} | {f.get('status')} | "
                f"{f.get('expected_state')} | {f.get('live_state') or '—(missing projection)'} | "
                f"{f.get('expected_vision_ir_version')} | {f.get('live_vision_ir_version') or '—'} |"
            )
        lines.append("")
        lines.append("**Remediation:** live projection row missing or stale vs event replay. Non-destructive check (`conduit.check_projection_drift()`); backfill `conduit.work_request_state` via projector/replay, or archive the WR if it is a stale test artifact. Detail: `GET /wr/:id/projection-drift` on conduit-srv (3104).")
    else:
        lines.append("\nNone — active work-request projections match event replay.")
    lines.append("")

    lines.append("## 5. Stale role leases (plan 1286)")
    rl_raw = findings.get("role_leases")
    rl_unavailable = rl_raw is None
    rl = rl_raw or []
    if rl_unavailable:
        lines.append("\n**Scan unavailable** — nebula-srv `/api/role-leases/stale` unreachable on this run; role-lease check skipped.")
    elif rl:
        lines += [
            "",
            "| role | channel | window_end | budget_units | consumed_units | expires_at |",
            "|---|---|---|---|---|---|",
        ]
        for item in rl:
            lines.append(
                f"| {item.get('role')} | {item.get('channel')} | "
                f"{item.get('window_end')} | {item.get('budget_units') or '—'} | "
                f"{item.get('consumed_units')} | {item.get('expires_at')} |"
            )
        lines.append("")
        lines.append(f"**{len(rl)} stale lease(s).** Remediation: the role's session ended without revoking — release via `POST /api/role-leases/:id/revoke` on nebula-srv (:3101) so another session can acquire it.")
    else:
        lines.append("\nNone — no stale role leases.")
    lines.append("")
    lines.append("---")
    lines.append("_Automated sweep — generated by `nexus-pipeline-health.timer`. Findings post only when the finding set changes; replies to triage below._")
    return "\n".join(lines)


def resolve_user_uuid(assembly_url, role):
    import urllib.request
    us = json.load(urllib.request.urlopen(f"{assembly_url}/api/users", timeout=10))
    uid = next((u["id"] for u in us if str(u.get("name", "")).lower() == role.lower()), "")
    if not uid:
        raise RuntimeError(f"could not resolve Assembly user UUID for role '{role}'")
    return uid


def post_thread(assembly_url, forum, title, body, role, model):
    import urllib.request
    uid = resolve_user_uuid(assembly_url, role)
    payload = {
        "title": title,
        "body": body,
        "postedById": uid,
        "role": role,
        "model": model,
    }
    req = urllib.request.Request(
        f"{assembly_url}/api/forums/{forum}/threads",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"signature": None, "threadId": None, "postedAt": None}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--forum", default=DEFAULT_FORUM)
    ap.add_argument("--role", default=DEFAULT_ROLE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--assembly-url", default=DEFAULT_ASSEMBLY_URL)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--conduit-url", default=DEFAULT_CONDUIT_URL)
    ap.add_argument("--nebula-url", default=DEFAULT_NEBULA_URL)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    args = ap.parse_args()

    if args.help:
        print(__doc__)
        return 0

    try:
        conn = psycopg2.connect(**DB)
        cur = conn.cursor()
        findings = run_checks(cur)
        cur.close()
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: health check query failed: {e}", file=sys.stderr)
        return 1

    # 4th check: projection-vs-replay drift via conduit-srv HTTP (plan 1285).
    # Returns None (skipped) when conduit-srv is unreachable — sweep continues.
    findings["projection"] = fetch_projection_drift(args.conduit_url)

    # 5th check: stale role leases via nebula-srv HTTP (plan 1286).
    findings["role_leases"] = fetch_role_lease_stale(args.nebula_url)

    # finding-record routing (to-do 299fce45): flagged drift plans get
    # type:finding records to:architect + to:watchdog. Dry-run only reports;
    # live runs emit after the forum post so a failed post still emits.
    flagged_rows = [r for r in findings["drift"] if r[6]]
    if args.dry_run:
        conn2 = psycopg2.connect(**DB)
        emit_drift_findings(conn2, flagged_rows, args.nebula_url, args.role, args.model, dry_run=True)
        conn2.close()

    sig = signature(findings)
    total = sum(len(v) for v in findings.values() if v is not None)

    if args.dry_run:
        print(build_report(findings, args.role, args.model))
        print(f"\n[dry-run] findings={total} signature={sig} — no post, state untouched")
        return 0

    state = load_state(args.state)
    prev_sig = state.get("signature")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

    if total == 0:
        # Nothing wrong now. If we previously reported findings, post a short
        # resolution so the thread trail shows closure. The resolution post is
        # independent of --quiet (it is the audit trail, not status chatter).
        if prev_sig:
            try:
                proj_txt = (
                    "0 projection drift"
                    if findings.get("projection") is not None
                    else "projection scan unavailable (conduit-srv down)"
                )
                rl_txt = (
                    "0 stale role leases"
                    if findings.get("role_leases") is not None
                    else "role-lease scan unavailable (nebula-srv down)"
                )
                post_thread(
                    args.assembly_url, args.forum,
                    f"Pipeline health RESOLVED ({now[:10]})",
                    f"Automated sweep: all pipeline health checks are clean "
                    f"(0 blocked, 0 drift, 0 flagged, {proj_txt}, {rl_txt}).",
                    args.role, args.model,
                )
                if not args.quiet:
                    print("posted resolution thread (all clear)")
            except Exception as e:  # noqa: BLE001
                # keep state so the resolution is retried next run
                print(f"WARN: resolution post failed: {e}", file=sys.stderr)
                return 1
        save_state(args.state, {"signature": None, "threadId": None, "postedAt": now})
        if not args.quiet:
            print("health sweep: all clear (0 findings)")
        return 0

    # Findings exist.
    if prev_sig == sig and not args.force:
        if not args.quiet:
            print(f"health sweep: {total} findings, signature unchanged ({sig}) — no post")
        return 0

    proj = len(findings.get("projection") or [])
    proj_label = "n/a" if findings.get("projection") is None else str(proj)
    rl_count = len(findings.get("role_leases") or [])
    rl_label = "n/a" if findings.get("role_leases") is None else str(rl_count)
    title = (
        f"Pipeline health: {total} finding(s) — blocked {len(findings['blocked'])}, "
        f"drift {len(findings['drift'])}, flagged {len(findings['flagged'])}, "
        f"projection {proj_label}, role-lease {rl_label} ({now[:10]})"
    )
    try:
        thread = post_thread(
            args.assembly_url, args.forum, title,
            build_report(findings, args.role, args.model),
            args.role, args.model,
        )
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: forum post failed: {e}", file=sys.stderr)
        return 1

    save_state(args.state, {"signature": sig, "threadId": thread.get("id"), "postedAt": now})
    if not args.quiet:
        print(f"posted findings thread {thread.get('id')} ({total} findings)")

    # emit tag-routed finding records for flagged drift plans (deduped)
    if flagged_rows:
        try:
            conn3 = psycopg2.connect(**DB)
            emitted, skipped = emit_drift_findings(
                conn3, flagged_rows, args.nebula_url, args.role, args.model
            )
            conn3.close()
        except Exception as e:  # noqa: BLE001
            print(f"WARN: finding-record emission failed: {e}", file=sys.stderr)
            emitted = skipped = 0
        if not args.quiet:
            print(f"finding records: emitted {emitted}, skipped (already open) {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
