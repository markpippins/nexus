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

### 1.1.1 Measured root cause (2026-11-28): it is the Wi-Fi, and a reboot is not the fix

The short `.209` flaps are 802.11 re-associations on `wlan0`, not a wedged host. Measured
from `titanium` plus the host's own journal after four flaps in one morning
(03:56, 06:28, 06:49, 08:10 EST):

| Evidence | Value |
|---|---|
| uplink | `wlan0 UP 192.168.1.209/24`; **`eth0 DOWN`, carrier `0`, 0 packets** — no cable |
| boot | `uptime -s` = 2026-11-27 17:40:28 — **one boot covers every morning flap** |
| AP steering | `wpa_supplicant: wlan0: WNM: Disassociation Imminent - Disassociation Timer 0` immediately before each drop |
| mechanism | `supplicant interface state: completed -> 4way_handshake -> completed`, then `dhcp4 (wlan0): state changed no lease` → new lease, **same** `192.168.1.209` |
| rate | **30** completed associations in 15 h; the two-minute probe caught 4 of them |
| driver | `brcmfmac: brcmf_cfg80211_set_power_mgmt: power save enabled` |
| power/thermal | `get_throttled=0x0`, `temp=56.0'C` — not a PSU, thermal or storage fault |

A 2-minute probe sees a lost association as `lan=down` for exactly one tick, which is why
every alert in the journal pairs with a recovery two minutes later: the four alerts are
four *sampled* events, not four two-minute outages. Actual unavailability is seconds.

**A reboot is not the fix.** The 17:40 boot is the boot all four flaps happened on, and the
2026-11-24/25 recoveries both came back without a LAN identity. A reboot re-associates
once; the AP keeps steering the client and the pattern resumes within hours. Do not power
cycle for this.

Fix, in order of preference:

1. **Cable it.** `eth0` is down with no carrier; a wired link removes this class entirely.
   Leave `wlan0` as failover if you want one.
2. **If it must stay on Wi-Fi:** disable power save
   (`nmcli con modify <con> 802-11-wireless.powersave 2`), pin the BSSID
   (`802-11-wireless.bssid <ap-mac>`) so the client stops following WNM steering, and turn
   off the router's band-steering / "roaming assist".
3. **If neither is possible,** treat one `lan=down` tick as one lost association rather than
two minutes of downtime when triaging, and expect the alerts to keep arriving at this rate.

Note that `titanium` reaches the host over its own `wlp3s0` (`192.168.1.71`), so an
association problem on either end looks identical from the probe. §1.1.1 was written from
the *host's* journal for exactly that reason — never diagnose this from the probe alone.

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

**A notice that could not be delivered is retried, and it is not green.** If the forum post
(or webhook, or `--alert-cmd`) fails on a transition, the tick exits **3** — deliberately not
in the unit's `SuccessExitStatus` — and leaves the recorded status at the previous verdict, so
the next tick sees the same transition and sends it again. That is why a failed send cannot
quietly consume a down *or* a recovery: the state only advances after a delivery succeeds.
Before this, the exception escaped as a traceback with exit 1, which `SuccessExitStatus=0 1`
reported as a successful tick — a lost notice looked exactly like a delivered one.

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
2026-11-25.

> **Do not read a `422` or `404` as "write access is granted".** GitHub validates the request
> body before it authorizes the token, and fine-grained tokens can read any **public**
> repository regardless of their repository selection. So `POST /issues` with a blank title
> returning `422`, or an issue/label read on an out-of-scope public repo returning `200`,
> proves nothing about permissions — both were misread as "Issues: Read and write is
> granted" on 2026-11-25 and sent the operator chasing a permission that was never
> installed. Only two things are decisive: a **write to an out-of-scope repository**
> returning `403` (the second command above), and the permission list on the token's own
> settings page. Judge a token by those. Editing a fine-grained token keeps its value, so a permission fix does **not**
require reinstalling it; but the edit form has a known defect that can reset Repository
access to "All repositories" on save, which is what the `atomic-go` check is for.

Give each host its **own** token. A token copied to a runner host is a credential that host
can use against the account, and sharing one value between `titanium` and `vanadium` means
compromising either compromises both.

```bash
python3 bin/vanadium_runner_watchdog.py --check --dry-run   # print the decision, write nothing
python3 bin/vanadium_runner_watchdog.py --check --json      # machine-readable
python3 bin/vanadium_runner_watchdog.py --check --forum     # alert on a transition (the timer)
python3 bin/vanadium_runner_watchdog.py --check --forum --maintenance-window 02:20-02:50
                                                            # hold alerts through a reboot
python3 bin/vanadium_runner_watchdog.py --check --alert-cmd 'cat >/tmp/watchdog-alert'
                                                            # a TEST tick: never posts (§9.2)
```

#### The workstation token — what `gh` on `titanium` needs

The table above is the **probe** credential: one call, read-only, unattended on a timer.
The other credential on the same machine is the operator's **workstation** token — the one
`gh` uses to push branches, open and merge PRs, and read CI. It legitimately needs more,
but it must still be a fine-grained PAT scoped to `markpippins/nexus` alone, never a
classic `repo` token.

| setting | value | why it is needed |
|---|---|---|
| Repository access | **Only select repositories** → `markpippins/nexus` | campaign work never touches another repo; a leaked token stays worthless elsewhere |
| Administration | **Read-only** | `GET /actions/runners`, the same endpoint the probe uses |
| Contents | **Read and write** | push branches and tags, read files at a ref |
| Pull requests | **Read and write** | open, update and merge a PR; read check runs and review state |
| Metadata | **Read-only** (mandatory) | granted automatically with every fine-grained token |
| Actions | *optional* — **Read and write** | needed only to rerun or cancel a workflow run; reading status and logs works with **Read-only** |
| Issues | *optional* — **Read-only** | only if the operator wants to read issues; no campaign step writes issues |
| Every other permission | **No access** | |

**`Git SSH keys` is an account-level permission, not a repository one.** Fine-grained PATs
split permissions into *repository* and *account* scopes, and SSH-key management lives in
the account half — it cannot be granted for `markpippins/nexus` alone. So no repo-scoped
token can add, read or rotate deploy keys, and running such a step means switching to a
credential with account scope, which is a deliberate decision, not a checkbox on the token
edit form. Prefer an SSH key on the host or a GitHub App over widening a PAT.

Evidence for the optional `Actions` row (2026-11-25): with `Actions` at **Read-only** the
campaign could read job annotations and run rollups but `POST
/repos/{owner}/{repo}/actions/runs/{id}/force-cancel` returned `403 Resource not
accessible by personal access token`, so a stuck run could not be rerun or cancelled from
the workstation. If a campaign depends on rerunning failed jobs, grant it; otherwise leave
it out.

Editing a token keeps its value, so adding a scope never requires reinstalling it — but
re-run the two verification commands above afterwards, because the edit form can silently
reset Repository access to **All repositories**.

Channels, first configured wins: `--webhook-url` (or `NEXUS_ALERT_WEBHOOK`, POSTs
`{"text": …}`), `--alert-cmd` (or `NEXUS_ALERT_CMD`, message on stdin), `--forum` (or
`NEXUS_ALERT_FORUM=1`, Assembly post to `issues-and-open-questions`). The down message names
this runbook and restates the "do not relabel `titanium`" rule, so the alert itself is
actionable.

**The forum is opt-in, and a tick with no channel refuses.** `--forum` is what lets a tick
post to Assembly; a run that names no channel at all exits **2** before probing and posts
nothing. That guard exists because of a real incident: on 2026-11-27T23:44:33Z a run made
only to check the probe named no channel, inherited the old forum default, and put a
real-looking "runner offline" notice in the issues forum — a test tick indistinguishable
from an outage except by its timestamp. It had to be retitled as an artifact and
cross-referenced; the guard is cheaper than that correction was.

> **Test ticks must pass `--alert-cmd`.** Anything that is not the timer — a drill, a
> replay, a probe check from an agent — runs
> `--check --alert-cmd 'cat >/tmp/watchdog-alert'` (or `--alert-cmd true` to discard the
> text). That keeps the message on the local filesystem or nowhere instead of the forum,
> and it still exercises the real delivery path, including exit 3 when the command fails.
> `--dry-run` is for looking only: it writes no state and delivers nothing. Never run a
> live `--check` without a channel, and never point a test at `--forum`.

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
~/.config/systemd/user/vanadium-runner-watchdog.service   (Type=oneshot, ExecStart=… --check --forum, SuccessExitStatus=0 1)
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

### 9.2 Holding alerts through a maintenance window

A scheduled reboot takes `vanadium` offline for a minute or two. The two-minute timer sees
the drop and then the return, so it pages **twice** — once `down`, once `recovered` — for
every reboot. `--maintenance-window HH:MM-HH:MM` (env
`NEXUS_WATCHDOG_MAINTENANCE_WINDOW`) suppresses delivery of alerts raised inside the window
instead.

| behavior | what it means |
|---|---|
| down alert raised inside the window | held, not delivered; recorded as `pending_alert` in the state file |
| `lan-down` raised inside the window (runner online, address dead) | held the same way, and the debt is carried while the runner is *healthy* — the runner being up says nothing about its address, so this debt is not discarded on that basis |
| window closes, runner still down | the held `down` alert is delivered **once**, prefixed with the window it was held through and how long the runner has been down |
| window closes, runner online, LAN still dead | the held `lan-down` alert is delivered **once**, prefixed the same way — it is still true |
| window closes, runner back and LAN back | nothing is delivered — the recovery notice is dropped, as is any debt the recovery has answered |
| probe failure (blind watchdog) inside the window | **never** held; a reboot does not make the GitHub API unreadable |
| malformed spec (`02:20`, `25:00-26:00`, …) | a configuration error (exit 2), not a silently disabled window |

The window delays an outage; it never hides one. That is the difference between this and a
blanket mute, and it is why the pending alert is re-sent rather than swallowed.

Arrange it so the window covers the reboot *and* the runner's return, and keep it clear of
the 03:00–04:30 backup chain (`pg-escape-hatch` 03:10, `mysql-backup` 03:45,
`mongo-backup` 04:05, `backup-vanadium-ci` 04:15). For a 02:30 reboot, `02:20-02:50`
leaves margin on both ends without touching them.

Arm it in the user unit (host local time — the same clock the reboot schedule uses):

```ini
# ~/.config/systemd/user/vanadium-runner-watchdog.service
[Service]
Environment=NEXUS_WATCHDOG_MAINTENANCE_WINDOW=02:20-02:50
```

or append the flag to `ExecStart` (`--maintenance-window 02:20-02:50`). Either way:

```bash
systemctl --user daemon-reload
systemctl --user restart vanadium-runner-watchdog.service   # or wait for the next tick
python3 bin/vanadium_runner_watchdog.py --check --dry-run --maintenance-window 02:20-02:50
```

The last command prints `[maintenance 02:20-02:50] holding the …` when run inside the
window, and writes nothing (`--dry-run`). More than one window is comma-separated
(`02:20-02:50,14:00-14:15`); a window whose start is after its end crosses midnight
(`23:50-00:10`). It is **off by default**: with no window set, behaviour is exactly the
transition alerting described above, and the committed unit stays inert.
