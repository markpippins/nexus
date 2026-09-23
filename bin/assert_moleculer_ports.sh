#!/usr/bin/env bash
# assert_moleculer_ports.sh — the local-moleculer gate, as one loud command.
#
# Doctrine: moleculer does NOT run locally on titanium (operator directive
# 2026-09-22: the candidate-tier lane runs containerized — cand-broker on
# host :14080 — and a future three-distribution comparison runs on barium
# when it returns). This script is the ENFORCEMENT of that posture: it fails
# loudly if any ratifier port from moleculer/PORT-MAP.md is bound on this
# host, so an accidental local moleculer start fails here instead of
# silently polluting M1 traffic-canary evidence (the M1 zero-window and the
# lead-engineer's lane-split evidence both depend on "moleculer doesn't run
# locally" being enforced, not assumed).
#
# What it checks (ground truth, not unit state):
#   1. ss -ltn — none of the mapped moleculer ports may be BOUND.
#   2. ss -ltnp — a bound port must name a process; a process whose command
#      line matches the moleculer runner is refused EVEN IF it bound a
#      non-mapped port (catches moleculer defaulting to a random port).
#   3. systemctl --user — every moleculer-*.service unit must be disabled.
#
# Exception path (documented, time-boxed): running the search canary locally
# requires an operator-approved exception. Set NEXUS_MOLECULER_EXCEPTION_DOC
# to a file containing a line "EXCEPTION-UNTIL: <ISO date>" before starting
# the app; the gate then passes WITH a loud notice while the file is valid,
# and fails again the day after the date. Nothing else loosens the gate.
#
# Usage:
#   bin/assert_moleculer_ports.sh              # exit 0 = posture holds
#   bin/assert_moleculer_ports.sh --json       # machine-readable report
#
# CI/PC note: ss is in iproute2 (present on ubuntu runners and titanium);
# the tests stub it via a fake bin dir on PATH (bin/tests/).
set -u

JSON=0
[ "${1:-}" = "--json" ] && JSON=1

# The ratified moleculer port map (moleculer/PORT-MAP.md; mirrored in
# jvm/ARCHITECTURE.md Port Allocation). Update BOTH docs and this list
# together per the freeze discipline.
MAPPED_PORTS="4050 4060 4080 4100 4106 4109 4114 4170"

SS_BIN="${SS_BIN-$(command -v ss || true)}"   # dash-form: empty override = simulate absence (tests)
failures=()
notices=()
exception_active=0
exception_until=""

# ── exception window (time-boxed, operator-documented) ────────────────────
if [ -n "${NEXUS_MOLECULER_EXCEPTION_DOC:-}" ] && [ -f "${NEXUS_MOLECULER_EXCEPTION_DOC}" ]; then
    exception_until="$(grep -E '^EXCEPTION-UNTIL:' "${NEXUS_MOLECULER_EXCEPTION_DOC}" | head -1 | awk '{print $2}')"
    if [ -n "$exception_until" ]; then
        today="$(date -u +%Y-%m-%d)"
        if [ "$(printf '%s\n' "$today" "$exception_until" | sort | head -1)" = "$today" ]; then
            exception_active=1
            notices+=("EXCEPTION ACTIVE until $exception_until (doc: $NEXUS_MOLECULER_EXCEPTION_DOC) — moleculer bindings permitted under operator exception; canary evidence must cite this window")
        fi
    fi
fi

# ── check 1: mapped ports must be unbound ─────────────────────────────────
if [ -n "$SS_BIN" ]; then
    bound_lines="$("$SS_BIN" -ltn 2>/dev/null || true)"
    for p in $MAPPED_PORTS; do
        if printf '%s\n' "$bound_lines" | grep -qE "[:.]${p}[[:space:]]"; then
                if [ "$exception_active" = 1 ]; then
                    notices+=("port $p BOUND under active exception")
                else
                    failures+=("moleculer map port $p is BOUND on this host — local moleculer start detected; stop it or open a documented exception (see script header)")
                fi
        fi
    done

    # ── check 2: no moleculer runner process on the HOST at all ───────────
    # Host ps sees containerized processes too (shared kernel), so each hit
    # is classified via /proc/<pid>/cgroup: docker/containerd cgroup paths =
    # containerized lane = permitted (cand-broker et al). PROC_BASE is
    # overridable for hermetic tests.
    if [ "$exception_active" = 0 ]; then
        PROC_BASE="${PROC_BASE:-/proc}"
        proc_hits="$(ps -eo pid=,args= 2>/dev/null | grep -E 'moleculer-runner|moleculer-runner\.js' | grep -v grep || true)"
        if [ -n "$proc_hits" ]; then
            while IFS= read -r line; do
                pid="$(printf '%s' "$line" | awk '{print $1}')"
                cmd="$(printf '%s' "$line" | cut -c1-120)"
                if [ -n "${pid//[!0-9]/}" ] && grep -qaE 'docker|containerd|/docker-' "$PROC_BASE/$pid/cgroup" 2>/dev/null; then
                    notices+=("moleculer runner inside a container (pid $pid) — permitted (containerized lane)")
                else
                    failures+=("moleculer runner process on HOST: $cmd (containerized lanes must stay in their containers; this one is not in any container)")
                fi
            done <<< "$proc_hits"
        fi
    fi
else
    notices+=("ss not found — port checks skipped (install iproute2 for a complete gate)")
fi

# ── check 3: every moleculer unit disabled ─────────────────────────────────
SYSTEMCTL_BIN="$(command -v systemctl || true)"
if [ -n "$SYSTEMCTL_BIN" ]; then
    unit_list="$("$SYSTEMCTL_BIN" --user list-unit-files 'moleculer-*' --no-pager 2>/dev/null | awk '$1 ~ /\.service$/ {print $1}' || true)"
    for u in $unit_list; do
        # is-enabled exits 1 for 'disabled' (normal) — exit code is noise here.
        state="$("$SYSTEMCTL_BIN" --user is-enabled "$u" 2>/dev/null || true)"
        state="${state:-unknown}"
        case "$state" in
            disabled) : ;;
            enabled)  failures+=("unit $u is ENABLED — moleculer apps are disabled-by-default on this host (systemctl --user disable $u)") ;;
            *)        notices+=("unit $u state: $state (not 'disabled' — verify intent)") ;;
        esac
    done
fi

# ── report ─────────────────────────────────────────────────────────────────
if [ "$JSON" = 1 ]; then
    json_arr() {  # ["a","b"] — empty-safe under set -u (avoids the ${arr[@]:-} quirk)
        printf '['
        local first=1
        for x in "$@"; do
            [ "$first" = 1 ] || printf ','
            printf '"%s"' "${x//\"/\\\"}"
            first=0
        done
        printf ']'
    }
    ports_json="$(printf '"%s",' $MAPPED_PORTS | sed 's/,$//')"
    printf '{"ports": [%s], "failures": %s, "notices": %s, "exception_active": %s}\n' \
        "$ports_json" \
        "$(json_arr ${failures[@]+"${failures[@]}"})" \
        "$(json_arr ${notices[@]+"${notices[@]}"})" \
        "$exception_active"
    exit 0   # JSON mode: stdout is the report; failures still surface on stderr
fi

for n in ${notices[@]+"${notices[@]}"}; do echo "NOTE: $n" >&2; done

if [ "${#failures[@]}" -gt 0 ]; then
    for f in "${failures[@]}"; do echo "FAIL: $f" >&2; done
    echo "MOLECULER PORT GATE: REFUSED ($(( ${#failures[@]} )) violation(s)) — see moleculer/PORT-MAP.md host-posture section" >&2
    exit 1
fi

echo "MOLECULER PORT GATE: OK — ${MAPPED_PORTS} unbound, no host runner, units disabled${exception_until:+ (exception active until $exception_until)}"
exit 0
