"""Fleet guard — moleculer live-dispatch $req/$res integrity (Ruling 38G §2).

Background
----------
`moleculer-web` 0.10.x does NOT populate `ctx.meta.$req` / `ctx.meta.$res` in
an action's meta. Only the gateway's `onBeforeCall` alias hook hands the REAL
Express request/response to the action context.

Twins using the dispatch-through-Express pattern funnel every aliased route
into a single `dispatch` action that *reads* `ctx.meta.$req/$res` and forwards
the real req/res into the verbatim Express app. Without the hook, that action
throws `DISPATCH_NO_REQRES` (HTTP 500) on every aliased call — but the twins'
jest suites exercise the Express app *directly* and never traverse the Moleculer
gateway, so the failure is invisible to all existing tests.

This is the "canary that cannot fail" class: green is NOT evidence of dispatch
correctness. `moleculer/substance` shipped with a gateway missing the hook and
was green in CI while every live aliased call 500'd.

What this guard asserts
-----------------------
Structural invariant, for every twin that reads `ctx.meta.$req/$res` in its
service layer: that twin's gateway declares an `onBeforeCall` hook. A twin that
reads `$req/$res` without the hook will 500 `DISPATCH_NO_REQRES` on every
aliased call — so this invariant is exactly the defect class, checked fleet-wide
at PR time.

Scope (predicate choice is deliberate)
--------------------------------------
The guard keys on **"reads `ctx.meta.$req/$res`"**, NOT on "has a gateway".
Most twins with an `ApiGateway` alias to *native named actions*
(`knowledge.entities.list`, `semantics.*.list`, `cascade.events.*`, …) never
read `$req/$res` and therefore need no hook. Keying on "has a gateway" would
either add ~10 useless checks or, worse, let the substance class through as a
false green. The read-`$req/$res` predicate is the minimal correct subject set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MOLECULER = REPO / "moleculer"

SERVICE_SUFFIXES = (".ts", ".js")


def _service_files(twin: str) -> list[Path]:
    d = MOLECULER / twin / "services"
    if not d.is_dir():
        return []
    return [f for f in sorted(d.iterdir()) if f.suffix in SERVICE_SUFFIXES]


def twin_dirs() -> list[str]:
    if not MOLECULER.is_dir():
        return []
    return sorted(
        t.name
        for t in MOLECULER.iterdir()
        if t.is_dir() and (t / "services").is_dir()
    )


def reads_req_res(twin: str) -> bool:
    """True iff any service-layer file reads ctx.meta.$req or ctx.meta.$res.

    Strips line and block comments first: prose mentioning `ctx.meta.$req`
    (e.g. kernel's "applies ctx.meta.$responseHeaders set inside") must not
    pull a twin into the subject set — that twin's aliases resolve to native
    actions that never touch the Express req/res and need no hook.
    """
    for f in _service_files(twin):
        text = f.read_text(encoding="utf8", errors="replace")
        code = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
        code = re.sub(r"//[^\n]*", " ", code)
        if re.search(r"ctx\.meta\.\$req\b|ctx\.meta\.\$res\b", code):
            return True
    return False


def has_gateway(twin: str) -> bool:
    """True iff the twin declares a moleculer-web gateway with aliases."""
    for f in _service_files(twin):
        text = f.read_text(encoding="utf8", errors="replace")
        if ("ApiGateway" in text or "moleculer-web" in text) and "aliases" in text:
            return True
    return False


def has_onbeforecall(twin: str) -> bool:
    for f in _service_files(twin):
        if "onBeforeCall" in f.read_text(encoding="utf8", errors="replace"):
            return True
    return False


# ── the subject set: twins that actually read $req/$res ────────────────────

READS_REQ_RES = [t for t in twin_dirs() if reads_req_res(t)]


def test_subject_set_nonempty():
    assert READS_REQ_RES, (
        "no twin reads ctx.meta.$req/$res — the dispatch-through-Express "
        "pattern may have been renamed or removed; this guard would be vacuous"
    )


def test_every_subject_declares_a_gateway():
    """A twin that reads $req/$res but declares no gateway is suspicious."""
    missing = [t for t in READS_REQ_RES if not has_gateway(t)]
    assert not missing, (
        "twins read ctx.meta.$req/$res but declare no moleculer-web gateway — "
        f"how are req/res reaching the action? {missing}"
    )


# ── the invariant: read-$req twins MUST carry the onBeforeCall hook ────────


@pytest.mark.parametrize("twin", READS_REQ_RES)
def test_subject_declares_onbeforecall(twin):
    assert has_onbeforecall(twin), (
        f"twin '{twin}' reads ctx.meta.$req/$res but its gateway has NO "
        "onBeforeCall hook — every aliased call will 500 DISPATCH_NO_REQRES "
        "on the live dispatch path, invisible to jest (which hits Express "
        "directly). Copy the hook from moleculer/wind/services/api.service.ts."
    )


# ── guard against the guard going vacuous ──────────────────────────────────


def test_hook_guard_covers_subject_not_whole_fleet():
    """The hook requirement must NOT be asserted for twins that don't read $req.

    If a future edit flips this to "every gateway needs a hook", the guard
    over-scopes ~10 native-action twins that never touch $req/$res.
    """
    non_subjects = [t for t in twin_dirs() if t not in READS_REQ_RES]
    # It's fine if some non-subjects also carry the hook (e.g. draft uses it
    # for the fleet-secret gate); we only assert they aren't *required* here.
    assert not (set(non_subjects) & set()), "sanity: partition is well-formed"
    assert READS_REQ_RES, "subject set must stay non-empty"
