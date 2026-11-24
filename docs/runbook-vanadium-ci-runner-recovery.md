# Runbook — vanadium self-hosted CI runner: diagnose and recover headlessly

**Scope:** the `vanadium` GitHub Actions runner (labels `[self-hosted, vanadium]`) that
runs `.github/workflows/vanadium-sonar.yml`. Written 2026-11-24 after the runner went
offline mid-session and left required `sonar` checks queued with zero steps started on
every open PR.

**Key property of this failure:** the host can be *up* — answering ping and completing
TCP handshakes — while the runner is unusable. Do not conclude "the box is fine" from
reachability alone.

> **Every probe in this runbook is measured; the state it was taken in matters.**
> **While the runner was down** (from `titanium`): runner `offline`, ping replies, the
> TCP/22 banner timeout, closed-port RST timings, ARP, the queued-job state — §1. The
> SonarQube probe failed in that window too (`http=000` after 5 s), which is the wedge,
> not the network path: the host's userspace was frozen.
> **After the 2026-11-24 12:24Z reboot** (first successful login): the runner path,
> systemd unit and `svc.sh` behaviour — §5 — and the host-side SonarQube probe, which
> returned `http=200 {"version":"9.9.8.100196","status":"STARTING"}` — §2.
> **After the services settled, 2026-11-24T18:07Z, from `titanium`:** SonarQube `:9000`
> *is* reachable over the LAN — `{"id":"303924D4-AaA8RS13kXJj3H-5ijkS",
> "version":"9.9.8.100196","status":"UP"} http=200 time=0.135785`, TCP connect `rc=0`.
> `localhost:9000` on `titanium` answers nothing (`http=000`, curl exit 7) because the
> service is not local — always probe the LAN address. An earlier revision of this
> runbook concluded the mapping was "not reachable across the LAN"; that was wrong, and
> rested on the outage-window probe above.

## 1. Fingerprint — kernel alive, userspace wedged

Run from `titanium`:

```bash
gh api repos/markpippins/nexus/actions/runners \
  -q '.runners[] | .name + " " + .status + " busy=" + (.busy|tostring)'
ping -c 10 -i 0.3 -W 2 192.168.1.209 | tail -2
timeout 5 ssh -o BatchMode=yes -o ConnectTimeout=5 codex@vanadium 'uptime' ; echo "ssh rc=$?"
```

| Observation | Meaning |
|---|---|
| `vanadium offline`, `titanium online` | the runner agent is not heartbeating |
| ping replies, ARP `REACHABLE` | host is powered, NIC up, same address |
| `ssh` prints `Connection timed out during banner exchange` | **TCP/22 accepted, `sshd` never wrote its version string** |
| closed ports (80/443/3300/8085) answer `RST` in ~10ms | the kernel is healthy — this is not a dead NIC or a routing fault |
| `sonar` job `queued` with zero steps | GitHub cannot place the job: no online runner carries the `vanadium` label |

Kernel-healthy + no SSH banner + runner silent at the same moment means the host's
**userspace is stalled** — typically memory exhaustion, a stalled SD/USB root disk, or
I/O hang. There is no IPMI and no serial console on this host, and with `sshd` wedged
there is no remote way in.

## 1.1 The inverse fingerprint — runner online, LAN dead (2026-11-25/26)

Same host, opposite failure, and much easier to miss. Measured 2026-11-26T17:19Z
from `titanium`:

| Probe | Result | Meaning |
|---|---|---|
| `gh api …/actions/runners` | `vanadium online busy=false` | the agent is heartbeating to GitHub |
| last job on it | run `45857636526` `sonar`, success, started `16:19:32Z` | it ran a real 11-minute aarch64 scan an hour earlier |
| `ping 192.168.1.209`, `ip neigh` | 100 % loss, `INCOMPLETE` | nothing answers the address |
| `getent hosts vanadium.attlocal.net` | `192.168.1.209` | the router's mapping outlives the host |
| gateway `192.168.1.254` | reachable, 0 % loss | the LAN path *from titanium* is healthy |
| `pg-backup-to-vanadium.log` / `sync-vanadium.log` | `FAIL(preflight)` / `No route to host` | the only automated signal there was |

So: **outbound to GitHub works, the LAN identity does not.** CI stays green
because `vanadium-sonar` needs only `localhost:9000` on the box plus the GitHub
API — so a green `sonar` check is *not* evidence the host is healthy, and
`ssh`/`.209` are unavailable exactly when they are wanted.

The one channel that survives this fault is a job running on the runner itself.
**`.github/workflows/vanadium-diagnostic.yml` ships on the default branch** (landed
in PR #1078): `workflow_dispatch` only, `runs-on: [self-hosted, vanadium]`,
`permissions: contents: read`, `timeout-minutes: 10`, no checkout, no third-party
action. It is dispatch-only on purpose — vanadium is a single runner slot, so a
scheduled diagnostic would sit in the queue and starve the scans. Dispatch it
(**Actions → vanadium-diagnostic → Run workflow**; `sonar_check` defaults to true)
and read the **verdict** block in the job summary: it names the interface the
kernel would use for github.com and whether the host holds a `192.168.1.x`
address. The steps print

```bash
ip -br a; ip r; ip neigh; nmcli con show --active
ip -o route get 140.82.112.4   # the interface the kernel would use for github.com
uptime -s; last -x | head; journalctl -b -1 -n 80 --no-pager
vcgencmd get_throttled; df -h /; free -m
```

No PR is needed and the run commits nothing. Either way: no
sudo, no reboot step.

Read the **verdict** first: no `192.168.1.x` address means the host is
reaching GitHub over some other path (another SSID, a hotspot, an extender's own
NAT) and the fix is the association, not the runner; a `192.168.1.x` address
means the fault is at the ARP/DHCP/AP layer.

Do not reboot to "fix" this before reading that verdict. The host came back from
this outage twice (2026-11-24 22:02 and 2026-11-25 15:05 EST) without its LAN
address either time, and a Pi 5 has no out-of-band reset (§3).

## 2. Triage before touching the box

```bash
# Is it only the runner, or the whole host's userspace?
timeout 5 bash -c 'cat </dev/null >/dev/tcp/192.168.1.209/22' && echo "22 accepts"

# Are the SonarQube containers still reachable? Probe over the LAN first — measured
# working from titanium 2026-11-24T18:07Z, http=200 in 0.136 s, {"status":"UP"}.
# If this hangs or answers 000 while the host answers ping, the host is wedged (§1/§3)
# and the host-side probe below will confirm it.
curl -s -m 5 -w '\nhttp=%{http_code}\n' http://192.168.1.209:9000/api/system/status

# Host-side fallback (works even when the LAN path is suspect):
# measured 2026-11-24 12:24Z: {"version":"9.9.8.100196","status":"STARTING"} http=200.
# `status` reads STARTING for a few minutes after boot and UP once SonarQube has
# finished starting; STARTING is startup, not a hang.
ssh codex@vanadium 'curl -s -m 4 -w "http=%{http_code}\n" http://localhost:9000/api/system/status'
ssh codex@vanadium 'docker ps --format "{{.Names}} {{.Status}}" | grep vd-ci'
```

If SSH answers and you can log in, this is **not** this runbook — start the agent
service instead (§5). If SSH hangs at the banner, go to §3.

## 3. Headless recovery — power cycle

A Pi 5 has no out-of-band reset, so with `sshd` hung this is the only path. It needs
physical hands.

1. **Graceful attempt (10 s):** short-press the power button, then wait for the activity
   LED to go dark. While userspace is frozen this usually does nothing.
2. **Hard off:** hold the power button ≥5 s until the LED goes out. If the board is in a
   case without a usable button, unplug the USB-C PSU.
3. Wait 10 s, restore power, wait 60–90 s for boot.
4. Verify from `titanium`:
   ```bash
   ssh codex@vanadium 'uptime; free -m; df -h /'
   gh api repos/markpippins/nexus/actions/runners \
     -q '.runners[] | select(.name=="vanadium") | .name + " " + .status'
   ```

## 4. Confirm the runner is really back

`vanadium online` is necessary but not sufficient — confirm the agent can actually work:

```bash
ssh codex@vanadium 'systemctl status actions.runner.markpippins-nexus.vanadium.service --no-pager | head -5'
gh api repos/markpippins/nexus/actions/jobs/<job-id> -q '.status + "/" + (.conclusion // "-")'
```

The queued sonar job starts on its own when an online runner carrying the `vanadium`
label appears. If the queue has expired (GitHub drops long-queued jobs), re-trigger with
`gh run rerun <run-id> --failed`.

## 5. If the host is up but the agent is not running

**Measured 2026-11-24 12:24Z** (first successful login after the reboot): the runner is
installed at **`/home/codex/dev/actions-runner`**, not `~/actions-runner`, and runs as a
systemd unit —

```
actions.runner.markpippins-nexus.vanadium.service
  ExecStart=/home/codex/dev/actions-runner/runsvc.sh
  WorkingDirectory=/home/codex/dev/actions-runner
  User=codex
```

`svc.sh` is present in that directory but refuses to run unprivileged (`Must run as
sudo`), so drive it through systemd, or through `svc.sh` with elevated rights:

```bash
ssh codex@vanadium 'systemctl status actions.runner.markpippins-nexus.vanadium.service --no-pager | head -5'
ssh codex@vanadium 'sudo systemctl restart actions.runner.markpippins-nexus.vanadium.service'
ssh codex@vanadium 'cd /home/codex/dev/actions-runner && sudo ./svc.sh status'
ssh codex@vanadium 'ls -t /home/codex/dev/actions-runner/_diag | head -3'
```

An agent that starts and immediately drops to `offline` again is a
credentials/reachability problem (outbound 443 to GitHub), not a wedge — read the newest
`_diag/Runner_*.log`.

## 6. Root-cause capture (do this on the first boot after the wedge)

The wedge will recur if the cause is storage or power, so capture it before the evidence
rotates:

```bash
ssh codex@vanadium 'vcgencmd get_throttled'                       # 0x0 clean; non-zero = undervoltage/throttle history
ssh codex@vanadium "dmesg -T | grep -iE 'oom|out of memory|I/O error|mmc0|hung task|EXT4-fs error|under-voltage' | tail -30"
ssh codex@vanadium 'journalctl -b -1 -n 100 --no-pager'           # the wedged boot
ssh codex@vanadium 'df -h /; free -m; lsblk -o NAME,SIZE,ROTA,MODEL'
```

- `mmc0`/`EXT4-fs error`/`hung task` → the storage is failing; plan an SSD/reflash, not
  another reboot.
- non-zero `get_throttled` → PSU or cable undersized for the Pi 5.
- OOM without a single fat process → raise `vm.min_free_kbytes`/zram, and consider
  moving the runner off the box entirely.

## 7. Do not do these

| Tempting move | Why it is wrong |
|---|---|
| Add the `vanadium` label to the `titanium` runner | `bin/ci/sonar_preflight.py` then finds the SonarQube server unreachable (it runs on vanadium) and the job goes **green with a skip warning**. That converts a real scan into a pre-flight skip — a gate weakened, not passed. |
| `NEXUS_ALLOW_UNATTESTED_MERGE=1` | Operator-only bypass of the attestation gate in `bin/merge_pr.py`; it does not bypass checks, and agents must never set it. |
| Cancel the queued job to clear the red | A cancelled check is a **failure**; the rollup still cannot reach all-success, and the verdict is lost. |
| Merge with `--admin` / force the merge | `bin/merge_pr.py` Gate 2 requires every check COMPLETED and successful; overriding it lands untested code. |

## 8. Blast radius while it is down

`sonar` is a required check, so **every PR** sits at `N-1/N` with `sonar` queued and
cannot merge — including docs-only PRs. Triage: do not open new PRs unless they must
queue behind the outage; land them as a batch once the runner is back. Jobs already
queued resume automatically; note the run ids so they can be re-triggered if the queue
expires.

## 9. Watchdog — catch the next outage in minutes, not from a queued check

`bin/vanadium_runner_watchdog.py` is a one-shot probe sized for a systemd timer. It reads
`GET /repos/markpippins/nexus/actions/runners`, compares the runner with the status it
last recorded (default `/var/tmp/vanadium-runner-watchdog.json`), and alerts **only on a
transition** — down, recovered, or a runner that vanished from the API — so a two-minute
timer cannot page anyone twice for one outage. A probe failure still exits 2 and never
writes a runner status, so a GitHub API outage can never masquerade as a runner outage.

**A blind watchdog now says so.** Probe failures are counted in the same state file, and
at `--probe-alert-after` consecutive failures (default **3**, ~6 minutes on the two-minute
timer; env `NEXUS_WATCHDOG_PROBE_ALERT_AFTER`) the script posts **one** watchdog-health
alert — *"the watchdog is BLIND"* — naming the last error and the credential the endpoint
needs, then stays quiet until sight returns. The next successful probe posts a
`probe-recovered` notice and resets the counter. A probe failure during a known outage
keeps `status: offline` and does not re-page `down`.

Why this exists (2026-11-25): a fine-grained PAT was installed on `titanium` without
`Administration: Read-only`, so every tick exited 2 — the unit was `active`, the journal
said `status=2/INVALIDARGUMENT`, and **nothing alerted**. Installed-and-blind is the worst
state a watchdog can be in, so blindness is now an alertable condition in its own right.

### 9.1 The credential this probe requires — read this before changing it

`GET /repos/{owner}/{repo}/actions/runners` is an **administrative** endpoint (the live
response carries `X-Accepted-Github-Permissions: administration=read`). For a fine-grained
PAT that means exactly:

| setting | value |
|---|---|
| Repository access | **Only select repositories** → `markpippins/nexus` |
| Repository permissions | **Administration: Read-only** (Metadata: Read-only is mandatory/automatic) |
| Every other permission | **No access** |

`Actions: Read-only` is **not** sufficient and is not needed — the probe makes exactly one
call. Classic PATs need `repo` (account-wide; use them only where a fine-grained token is
unavailable). A token that reaches other repositories, or that carries write permissions
(`Issues: Read and write` and friends), is a worse credential than the one it replaced even
if the probe happens to work.

Verify a candidate credential before installing it anywhere — the first command must pass
while the second must fail:

```bash
gh api repos/markpippins/nexus/actions/runners -q '.runners[] | .name + " " + .status'
gh api repos/markpippins/atomic-go -q .full_name    # must 404/403 if the token is repo-scoped
python3 bin/vanadium_runner_watchdog.py --check --dry-run   # prints `vanadium runner: online`
```

A 403 `Resource not accessible by personal access token` on the first command means the
token lacks `Administration: Read-only` — the exact failure that blinded the watchdog on
2026-11-25. Editing a fine-grained token keeps its value, so a permission fix does **not**
require reinstalling it; but the edit form has a known defect that can reset Repository
access to "All repositories" on save, which is what the `atomic-go` check is for.

Give each host its **own** token. A token copied to a runner host is a credential that host
can use against the account, and sharing one value between `titanium` and `vanadium` means
compromising either compromises both.

```bash
python3 bin/vanadium_runner_watchdog.py --check --dry-run   # print the decision, write nothing
python3 bin/vanadium_runner_watchdog.py --check --json      # machine-readable
python3 bin/vanadium_runner_watchdog.py --check             # alert on a transition
```

Channels, first configured wins: `--webhook-url` (or `NEXUS_ALERT_WEBHOOK`, POSTs
`{"text": …}`), `--alert-cmd` (or `NEXUS_ALERT_CMD`, message on stdin), default Assembly
post to `issues-and-open-questions`. The down message names this runbook and restates the
"do not relabel `titanium`" rule, so the alert itself is actionable.

**Since 2026-11-26 it probes two things**, because this host has now failed in
both directions: the runners API (above) *and* one TCP connect to the LAN address
(`--lan-host` / `--lan-port`, default `192.168.1.209:22`). The second exists for
§1.1 — on 2026-11-25/26 the runner stayed online and kept scanning for two days
while its address was dead, and a runner-status-only probe is blind to exactly
that. The rules:

- **LAN breaks while the runner is online** → `alert=lan-down`: one message naming
  the backups it breaks and how to get a dump off the host (a throwaway job — no
  diagnostic workflow is in the repo yet), and when the last good check was (the
  poll cannot know the exact moment, only the window).
- **The host is offline** → one alert, not two: the LAN verdict is recorded
  silently, and a **return without its LAN identity** escalates on the check that
  sees the runner come back ("It came back without its LAN identity").
- **LAN recovers** → `lan-recovered`, which says to rerun the off-machine copy
  and the SQL sync.
- Exit 1 now means "runner online **and** LAN reachable" failed. `--no-lan-probe`
  restores the old runner-only behaviour. The state file gained `lan` /
  `lan_since`, and a run with the probe skipped keeps the last verdict rather
  than erasing it.

**Installed on `titanium` 2026-11-24** as a *user* unit (no root needed; `Linger=yes`
is already set for `codex`, so it runs without a login session):

```
~/.config/systemd/user/vanadium-runner-watchdog.service   (Type=oneshot, SuccessExitStatus=0 1)
~/.config/systemd/user/vanadium-runner-watchdog.timer     (OnBootSec=2min, OnUnitActiveSec=2min)
→ systemctl --user enable --now vanadium-runner-watchdog.timer
```

Observed: timer `active (waiting)`, one service run per two minutes with `systemctl
--user list-timers` showing the next elapse, and journal lines
`vanadium runner: online` / `Finished … (status=0/SUCCESS)`. State lives in
`/var/tmp/vanadium-runner-watchdog.json` (`{"status": "online", …}`).

**Not** installed on `vanadium` itself: it has no nexus checkout, and its `gh` is
intentionally **unauthenticated** — a credential there is reachable by untrusted CI code,
and one should be a separate fine-grained token scoped exactly as in §9.1. A watcher on
the host it watches also cannot report that host being wedged — the failure this runbook
exists for. So the probe belongs on `titanium`, which holds the credentials and stays up.

Source of truth is in the repo — `bin/vanadium-runner-watchdog.service` and
`bin/vanadium-runner-watchdog.timer`; each header carries its own deploy note. Install
from a checkout, no root:

```bash
cp bin/vanadium-runner-watchdog.service bin/vanadium-runner-watchdog.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now vanadium-runner-watchdog.timer
systemctl --user list-timers vanadium-runner-watchdog.timer --no-pager
```

It belongs on **titanium** — the machine that holds the `gh` credentials and stays
up — not on the Pi it is watching.
