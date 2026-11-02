#!/usr/bin/env bash
# pg-target-preflight.sh — prove the off-machine PG target is THERE before dumping.
#
# Why this exists (2026-11-02): vanadium was powered off overnight. The nightly
# backup enumerated 55 databases and dumped all of them (~90s, 637M for `nexus`
# alone), then died at the transfer step with
#
#   ssh: Could not resolve hostname vanadium: Name or service not known
#   FAIL: cannot create remote dir pg-backups/titanium on vanadium
#
# Both lines point the wrong way. "Cannot resolve" reads as a DNS defect and
# "cannot create remote dir" reads as a permissions problem on the remote
# path — the actual condition was that the host was not on the LAN, and the
# router had dropped its name with the lease. The miss cost a full dump cycle
# and a misdirected triage.
#
# This probes the target in layers and names the layer that failed, so an
# unreachable target reports as UNREACHABLE.
#
# Usage:
#   . bin/lib/pg-target-preflight.sh
#   if ! detail="$(pg_target_preflight)"; then
#     printf '%s\n' "$detail"     # human-readable, already multi-line
#     exit 1
#   fi
#
# Returns:
#   0  reachable: name resolves, the TCP port answers, ssh BatchMode succeeds
#   2  the name does not resolve (no /etc/hosts entry, no DNS answer)
#   3  the name resolves but the TCP port does not answer
#   4  the port answers but ssh itself fails (auth, host key, remote shell)
#
# Env:
#   REMOTE_HOST           target (default: vanadium)
#   REMOTE_USER           ssh user (default: none — ssh config default)
#   TARGET_PORT           port to probe (default: 22)
#   TARGET_PROBE_TIMEOUT  per-probe timeout in seconds (default: 5)
#
# Prints nothing on success; a multi-line reason on stdout on failure.

pg_target_preflight() {
  local host="${REMOTE_HOST:-vanadium}"
  local port="${TARGET_PORT:-22}"
  local user="${REMOTE_USER:-}"
  local tmo="${TARGET_PROBE_TIMEOUT:-5}"
  local target="$host"
  [ -n "$user" ] && target="${user}@${host}"

  # Layer 1 — name resolution. A powered-off host on a DHCP network takes its
  # name with it, so failure here means "look at the target", not "fix DNS".
  local resolved=""
  if [[ ! "$host" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
    resolved="$(getent hosts "$host" 2>/dev/null | awk 'NR==1{print $1}')"
    if [ -z "$resolved" ]; then
      printf '%s\n' \
        "target unreachable: '${host}' does not resolve (no /etc/hosts entry, no DNS answer)" \
        "  a host that is powered off or off the LAN takes its name with it — this is what a dead target looks like, and it is not proof that DNS is broken" \
        "  nothing was dumped: the run stops here instead of spending a dump cycle on an absent target"
      return 2
    fi
  else
    resolved="$host"
  fi

  # Layer 2 — TCP. Resolution succeeding while the port is silent isolates
  # "host is up but the service is not" from "host is gone".
  if ! timeout "$tmo" bash -c "exec 3<>/dev/tcp/${resolved}/${port}" 2>/dev/null; then
    printf '%s\n' \
      "target unreachable: ${host} resolves to ${resolved} but TCP/${port} does not answer" \
      "  the name is still registered, so the host may be down, still booting, or filtered on ${port}; nothing was dumped"
    return 3
  fi

  # Layer 3 — ssh. Reaching the port but failing the handshake is an auth or
  # host-key condition, and quoting ssh's own words is what makes it
  # diagnosable without re-running by hand.
  local err
  if ! err="$(ssh -o BatchMode=yes -o ConnectTimeout="$tmo" "$target" true 2>&1)"; then
    printf '%s\n' \
      "target unreachable: ${host} answers TCP/${port} but ssh failed" \
      "  ssh said: $(printf '%s' "$err" | tr '\n' ' ')"
    return 4
  fi

  return 0
}
