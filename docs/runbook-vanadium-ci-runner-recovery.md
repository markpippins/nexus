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
timer cannot page anyone twice for one outage. A probe failure exits 2 and touches
neither the state file nor any alert channel, so a GitHub API outage can never
masquerade as a runner outage.

```bash
python3 bin/vanadium_runner_watchdog.py --check --dry-run   # print the decision, write nothing
python3 bin/vanadium_runner_watchdog.py --check --json      # machine-readable
python3 bin/vanadium_runner_watchdog.py --check             # alert on a transition
```

Channels, first configured wins: `--webhook-url` (or `NEXUS_ALERT_WEBHOOK`, POSTs
`{"text": …}`), `--alert-cmd` (or `NEXUS_ALERT_CMD`, message on stdin), default Assembly
post to `issues-and-open-questions`. The down message names this runbook and restates the
"do not relabel `titanium`" rule, so the alert itself is actionable.

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

**Not** installed on `vanadium` itself: that host's `gh` answers `{"message": "Bad
credentials"}` (measured 2026-11-24) and it has no nexus checkout, so a unit there
could not read the runners API. A watcher on the host it watches also cannot report
that host being wedged — the failure this runbook exists for.

Timer sketch for a root/system install (the form used above, adapted):

```ini
# /etc/systemd/system/vanadium-runner-watchdog.service
[Unit]
Description=Alert when the vanadium CI runner is offline
After=network-online.target
[Service]
Type=oneshot
User=codex
ExecStart=/usr/bin/python3 /home/codex/dev/nexus/bin/vanadium_runner_watchdog.py --check
# the probe exits 1 while the runner is down: that is the alert path, not a unit failure
SuccessExitStatus=0 1
```

```ini
# /etc/systemd/system/vanadium-runner-watchdog.timer
[Unit]
Description=Probe the vanadium CI runner every 2 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
AccuracySec=30s
[Install]
WantedBy=timers.target
```

`systemctl enable --now vanadium-runner-watchdog.timer`. It belongs on **titanium** — the
machine that holds the `gh` credentials and stays up — not on the Pi it is watching.
