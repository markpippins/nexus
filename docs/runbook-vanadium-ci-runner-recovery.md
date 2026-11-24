# Runbook — vanadium self-hosted CI runner: diagnose and recover headlessly

**Scope:** the `vanadium` GitHub Actions runner (labels `[self-hosted, vanadium]`) that
runs `.github/workflows/vanadium-sonar.yml`. Written 2026-11-24 after the runner went
offline mid-session and left required `sonar` checks queued with zero steps started on
every open PR.

**Key property of this failure:** the host can be *up* — answering ping and completing
TCP handshakes — while the runner is unusable. Do not conclude "the box is fine" from
reachability alone.

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
# Are the SonarQube containers still reachable? (they live on vanadium)
curl -s --max-time 3 http://vanadium:9000/api/system/status || echo "sonar API unreachable"
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
ssh codex@vanadium 'cd ~/actions-runner && ./svc.sh status'
gh api repos/markpippins/nexus/actions/jobs/<job-id> -q '.status + "/" + (.conclusion // "-")'
```

The queued sonar job starts on its own when an online runner carrying the `vanadium`
label appears. If the queue has expired (GitHub drops long-queued jobs), re-trigger with
`gh run rerun <run-id> --failed`.

## 5. If the host is up but the agent is not running

```bash
ssh codex@vanadium 'cd ~/actions-runner && ./svc.sh status'
ssh codex@vanadium 'cd ~/actions-runner && sudo ./svc.sh start'   # or: systemctl start 'actions.runner.*'
ssh codex@vanadium 'pgrep -af Runner.Listener'
```

An agent that starts and immediately goes offline again is a credentials/reachability
problem (outbound 443 to GitHub), not a wedge — check `~/actions-runner/_diag/*.log`.

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
