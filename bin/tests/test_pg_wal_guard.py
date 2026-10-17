"""Hermetic tests for bin/check_pg_wal.py + bin/pg_wal_guard_wrap.py.

No database, no docker, no filesystem state outside tmp_path: the psql
invocation and the post-agent-record call are faked at the subprocess.run
boundary, and the wrapper state file is redirected.

What these guard against, in the order the incident e7a15424 exposed them:

  - the guard must fire on the *construction* defect (archiving enabled with
    nothing behind it) and not only on the visible symptom (a full disk);
  - it must NOT fire on the residual state we deliberately chose for this
    cluster (archive_mode=off), because a guard that cries wolf gets muted;
  - the archiver-progress rule must be silent when archiving is off, and
    silent when a backlog is below the meaningful floor, or it will fire
    during ordinary segment churn;
  - the tier-escalation escape from daily suppression has to work in both
    directions: same tier -> suppressed, escalation -> files immediately.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent  # worktree root


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CHECK = _load("check_pg_wal", REPO / "bin" / "check_pg_wal.py")
WRAP = _load("pg_wal_guard_wrap", REPO / "bin" / "pg_wal_guard_wrap.py")

GIB = 1024 ** 3


def measurement(**over) -> dict:
    """A healthy cluster measurement; override per test."""
    base = {
        "wal_bytes": 80 * 1024 ** 2,
        "seg_count": 12,
        "ready": 12,
        "done": 0,
        "archive_mode": "off",
        "archive_command": "",
        "archive_library": "",
        "archived_count": 0,
        "failed_count": 0,
        "last_archived_time": None,
        "last_failed_time": None,
        "data_directory": "/var/lib/postgresql/data",
        "max_wal_size": "1GB",
        "server_version": "17.11",
    }
    base.update(over)
    return base


def disk(free_gib: float = 173.0) -> dict:
    return {"measured": True, "mount": "/", "pgdata_host": "/pgdata",
            "free_bytes": int(free_gib * GIB), "total_bytes": 444 * GIB}


TH = CHECK.thresholds()


def dimensions(findings: list[dict]) -> set[str]:
    return {f["dimension"] for f in findings}


# ---------------------------------------------------------------------------
# Checker: row parsing
# ---------------------------------------------------------------------------

ROW = ("83886080|12|12|0|off|||0|0|||/var/lib/postgresql/data|1GB|17.11")


class TestParseRow:
    def test_healthy_row_parsed(self):
        m = CHECK.parse_row(ROW)
        assert m["wal_bytes"] == 83886080
        assert m["ready"] == 12
        assert m["archive_mode"] == "off"
        assert m["archive_command"] == ""
        assert m["data_directory"] == "/var/lib/postgresql/data"
        assert m["server_version"] == "17.11"

    def test_null_timestamps_become_none(self):
        assert CHECK.parse_row(ROW)["last_archived_time"] is None

    def test_short_row_is_a_tool_error_not_a_silent_zero(self):
        with pytest.raises(ValueError):
            CHECK.parse_row("1|2|3")


# ---------------------------------------------------------------------------
# Checker: classification
# ---------------------------------------------------------------------------

class TestClassification:
    def test_healthy_cluster_has_no_findings(self):
        assert CHECK.classify(measurement(), disk(), TH) == []

    def test_construction_rule_fires_on_incident_config(self):
        """archive_mode=on with empty command AND empty library is the root
        cause; it must fire immediately, before any growth."""
        m = measurement(archive_mode="on", archive_command="",
                        archive_library="")
        found = CHECK.classify(m, disk(), TH)
        assert "archiving_unimplemented" in dimensions(found)
        assert CHECK.overall_tier(found) == "critical"

    def test_construction_rule_still_fires_when_disk_is_still_fine(self):
        """The whole point: 30 days of headroom must not hide the defect."""
        m = measurement(archive_mode="on")
        found = CHECK.classify(m, disk(free_gib=400.0), TH)
        assert CHECK.overall_tier(found) == "critical"

    def test_archive_library_counts_as_a_target(self):
        m = measurement(archive_mode="on", archive_library="basic_archive")
        assert "archiving_unimplemented" not in dimensions(
            CHECK.classify(m, disk(), TH))

    def test_archive_command_counts_as_a_target(self):
        m = measurement(archive_mode="on", archive_command="wal-g wal-push %p")
        assert "archiving_unimplemented" not in dimensions(
            CHECK.classify(m, disk(), TH))

    def test_disabled_sentinel_is_not_mistaken_for_a_target(self):
        """PostgreSQL renders archive_command as '(disabled)' while
        archive_mode is off (GUC show hook, observed live). If that sentinel
        counted as a destination, the construction rule would go blind to
        the exact defect it exists to catch."""
        m = measurement(archive_mode="on", archive_command="(disabled)")
        assert CHECK.has_archive_target(m) is False
        assert "archiving_unimplemented" in dimensions(
            CHECK.classify(m, disk(), TH))

    def test_disabled_sentinel_alone_is_quiet(self):
        m = measurement(archive_mode="off", archive_command="(disabled)")
        assert CHECK.classify(m, disk(), TH) == []

    def test_disabled_archiving_is_not_a_finding(self):
        """Our deliberate post-incident state (Option B, 2026-10-17) must be
        quiet, or the guard gets muted within a day."""
        assert CHECK.classify(measurement(archive_mode="off"), disk(),
                              TH) == []

    def test_wal_size_warn_then_critical(self):
        warn = CHECK.classify(measurement(wal_bytes=25 * GIB), disk(), TH)
        crit = CHECK.classify(measurement(wal_bytes=156 * GIB), disk(), TH)
        assert [f["tier"] for f in warn] == ["warn"]
        assert [f["tier"] for f in crit] == ["critical"]

    def test_wal_size_below_warn_is_quiet(self):
        assert CHECK.classify(measurement(wal_bytes=19 * GIB), disk(),
                              TH) == []

    def test_ready_backlog_warn_then_critical(self):
        warn = CHECK.classify(measurement(ready=600), disk(), TH)
        crit = CHECK.classify(measurement(ready=10007), disk(), TH)
        assert [f["dimension"] for f in warn] == ["ready_backlog"]
        assert warn[0]["tier"] == "warn"
        assert crit[0]["tier"] == "critical"

    def test_ready_backlog_finding_names_the_archive_mode(self):
        """The backlog line must say whether archiving was even on — the
        two cases need completely different responses."""
        found = CHECK.classify(measurement(ready=10007, archive_mode="off"),
                               disk(), TH)
        backlog = [f for f in found if f["dimension"] == "ready_backlog"]
        assert len(backlog) == 1
        assert "archive_mode=off" in backlog[0]["message"]

    def test_archiver_failures_warn(self):
        m = measurement(archive_mode="on",
                        archive_command="wal-g wal-push %p", failed_count=7,
                        last_failed_time="2026-10-17 04:00:00+00")
        found = CHECK.classify(m, disk(), TH)
        assert "archiver_failures" in dimensions(found)
        assert [f["tier"] for f in found] == ["warn"]

    def test_failed_count_ignored_while_archiving_is_off(self):
        """A stale counter from when archiving was enabled must not persist
        as a permanent warning."""
        m = measurement(archive_mode="off", failed_count=7)
        assert CHECK.classify(m, disk(), TH) == []

    def test_incident_measurements_trip_every_dimension(self):
        """The exact numbers from e7a15424 (156 GB, 10,007 ready, 17 GB
        free, archiving enabled with no command)."""
        m = measurement(wal_bytes=156 * GIB, seg_count=10008, ready=10007,
                        archive_mode="on")
        found = CHECK.classify(m, disk(free_gib=17.0), TH)
        assert dimensions(found) == {"archiving_unimplemented", "wal_size",
                                     "ready_backlog", "disk_headroom"}
        assert CHECK.overall_tier(found) == "critical"

    def test_disk_warn_then_critical(self):
        warn = CHECK.classify(measurement(), disk(free_gib=40.0), TH)
        crit = CHECK.classify(measurement(), disk(free_gib=5.0), TH)
        assert dimensions(warn) == {"disk_headroom"}
        assert warn[0]["tier"] == "warn"
        assert crit[0]["tier"] == "critical"

    def test_unmeasured_disk_is_reported_not_skipped(self):
        found = CHECK.classify(measurement(),
                               {"measured": False, "reason": "statvfs denied"},
                               TH)
        assert dimensions(found) == {"disk_headroom"}
        assert "UNMEASURED" in found[0]["message"]
        assert "statvfs denied" in found[0]["message"]
        assert found[0]["tier"] == "warn"

    def test_overall_tier_prefers_critical(self):
        found = [{"dimension": "a", "tier": "warn", "message": ""},
                 {"dimension": "b", "tier": "critical", "message": ""}]
        assert CHECK.overall_tier(found) == "critical"
        assert CHECK.overall_tier([]) is None


class TestMountResolution:
    MOUNTS = (
        "sysfs /sys sysfs rw 0 0\n"
        "tmpfs /run tmpfs rw 0 0\n"
        "/dev/nvme0n1p2 / ext4 rw 0 0\n"
        "/dev/nvme0n1p3 /mnt/data ext4 rw 0 0\n"
        "/dev/nvme0n1p3 /mnt/data/nested ext4 rw 0 0\n"
    )

    def test_root_mount_found(self):
        assert CHECK.resolve_mount("/home/codex/dev/pgsql/pgdata",
                                   self.MOUNTS) == "/"

    def test_longest_prefix_wins_over_nested_bind(self):
        assert CHECK.resolve_mount("/mnt/data/nested/pgdata",
                                   self.MOUNTS) == "/mnt/data/nested"

    def test_sibling_with_shared_prefix_is_not_matched(self):
        """`/mnt/database` must not match a mount at `/mnt/data`."""
        assert CHECK.resolve_mount("/mnt/database/pgdata", self.MOUNTS) == "/"

    def test_no_matching_mount_is_none(self):
        assert CHECK.resolve_mount("/nowhere", "sysfs /sys sysfs rw 0 0") is None

    def test_measure_disk_reports_real_filesystem(self, tmp_path):
        pgdata = tmp_path / "pgdata"
        pgdata.mkdir()
        mounts = tmp_path / "mounts"
        mounts.write_text(f"/dev/sdX {tmp_path} ext4 rw 0 0\n")
        d = CHECK.measure_disk(pgdata_host=str(pgdata),
                               mounts_path=str(mounts))
        assert d["measured"] is True
        assert d["mount"] == str(tmp_path)
        assert d["free_bytes"] > 0 and d["total_bytes"] >= d["free_bytes"]

    def test_measure_disk_missing_mounts_file_is_reported(self, tmp_path):
        d = CHECK.measure_disk(pgdata_host=str(tmp_path),
                               mounts_path=str(tmp_path / "absent"))
        assert d["measured"] is False
        assert "unreadable" in d["reason"]

    def test_nonexistent_pgdata_path_is_not_silently_measured(self):
        """A wrong PGDATA_HOST still prefix-matches '/' in /proc/mounts, so
        without an existence check the guard would report a healthy disk
        figure for a filesystem that has nothing to do with pgdata. A guard
        measuring the wrong thing while looking green is the failure this
        whole file exists to prevent."""
        d = CHECK.measure_disk(pgdata_host="/nonexistent/pgdata")
        assert d["measured"] is False
        assert "does not exist" in d["reason"]
        found = CHECK.classify(measurement(), d, TH)
        assert dimensions(found) == {"disk_headroom"}
        assert "UNMEASURED" in found[0]["message"]

    def test_existing_path_is_measured(self, tmp_path):
        """The positive case, on a path this test controls — deliberately
        not the host's real pgdata, so the guard suite stays runnable on a
        bare CI runner."""
        pgdata = tmp_path / "pgdata"
        pgdata.mkdir()
        mounts = tmp_path / "mounts"
        mounts.write_text(f"/dev/sdX {tmp_path} ext4 rw 0 0\n")
        d = CHECK.measure_disk(pgdata_host=str(pgdata),
                               mounts_path=str(mounts))
        assert d["measured"] is True
        assert d["free_bytes"] > 0


# ---------------------------------------------------------------------------
# Wrapper: green-heartbeat + thresholds decisions
# ---------------------------------------------------------------------------

class TestGreenDecisionLockstep:
    def test_decide_green_action_matches_durability_wrapper(self):
        other = _load("record_durability_wrap_lockstep",
                      REPO / "bin" / "record_durability_wrap.py")
        states = [{}, {"ever_green": False, "last_drift_recorded": "2026-W41"},
                  {"ever_green": True, "last_green_recorded": "2026-W41"},
                  {"ever_green": True, "last_green_recorded": "2026-W42"}]
        for state in states:
            for week in ("2026-W42", "2026-W43"):
                assert WRAP.decide_green_action(state, week) == \
                    other.decide_green_action(state, week), (state, week)


class TestArchiverStall:
    def test_silent_when_archiving_is_off(self):
        assert WRAP.decide_archiver_stall(
            {"archived_count": 0}, measurement(archive_mode="off"), 10) is None

    def test_silent_when_misconfigured_since_the_checker_owns_that(self):
        m = measurement(archive_mode="on", ready=5000)
        assert WRAP.decide_archiver_stall({"archived_count": 0}, m, 10) is None

    def test_silent_below_backlog_floor(self):
        m = measurement(archive_mode="on", archive_command="/bin/true",
                        ready=2)
        assert WRAP.decide_archiver_stall({"archived_count": 0}, m, 10) is None

    def test_silent_on_first_sighting(self):
        """No previous snapshot means no evidence of a flat line yet."""
        m = measurement(archive_mode="on", archive_command="/bin/true",
                        ready=900)
        assert WRAP.decide_archiver_stall(None, m, 10) is None

    def test_silent_while_progressing(self):
        m = measurement(archive_mode="on", archive_command="/bin/true",
                        ready=900, archived_count=400)
        assert WRAP.decide_archiver_stall({"archived_count": 380}, m,
                                          10) is None

    def test_fires_when_counter_is_flat_with_a_backlog(self):
        m = measurement(archive_mode="on", archive_command="/bin/true",
                        ready=900, archived_count=400,
                        last_archived_time="2026-10-01 00:00:00+00")
        found = WRAP.decide_archiver_stall({"archived_count": 400}, m, 10)
        assert found["dimension"] == "archiver_stall"
        assert found["tier"] == "critical"
        assert "ARCHIV" not in found["message"]  # readable, not shouted
        assert "no progress" in found["message"]
        assert WRAP.INCIDENT_REF in found["message"]

    def test_archive_always_also_checked(self):
        m = measurement(archive_mode="always", archive_command="/bin/true",
                        ready=900, archived_count=5)
        assert WRAP.decide_archiver_stall({"archived_count": 5}, m,
                                          10) is not None


class TestEscalation:
    def test_critical_escalates_when_only_warn_was_alerted(self):
        assert WRAP.escalated({"last_alerted_tier": "warn"}, "critical") is True

    def test_critical_escalates_when_nothing_alerted(self):
        assert WRAP.escalated({}, "critical") is True

    def test_same_critical_does_not_escalate(self):
        assert WRAP.escalated({"last_alerted_tier": "critical"},
                              "critical") is False

    def test_warn_never_escalates(self):
        assert WRAP.escalated({}, "warn") is False


# ---------------------------------------------------------------------------
# Wrapper: end-to-end main() flows with faked subprocess
# ---------------------------------------------------------------------------

def payload(**over) -> str:
    base = {
        "ok": True,
        "tier": None,
        "measurement": measurement(),
        "disk": disk(),
        "thresholds": TH,
        "findings": [],
    }
    base.update(over)
    return json.dumps(base)


FINDING = {"dimension": "wal_size", "tier": "critical",
           "message": "pg_wal is 156.0 GB (critical threshold 60.0 GB)"}
CRITICAL_PAYLOAD = payload(
    ok=False, tier="critical", findings=[FINDING],
    measurement=measurement(wal_bytes=156 * GIB))
WARN_PAYLOAD = payload(
    ok=False, tier="warn", findings=[
        {"dimension": "wal_size", "tier": "warn", "message": "pg_wal is 25 GB"}],
    measurement=measurement(wal_bytes=25 * GIB))


class FakeRuns:
    def __init__(self, checker_rc: int, checker_out: str, poster_rc: int = 0):
        self.checker_rc = checker_rc
        self.checker_out = checker_out
        self.poster_rc = poster_rc
        self.posters: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        if any(str(a).endswith("check_pg_wal.py") for a in cmd):
            rc, out = self.checker_rc, self.checker_out

            class R:
                returncode = rc
                stdout = out
                stderr = ""
            return R()
        self.posters.append([str(a) for a in cmd])
        poster_rc = self.poster_rc

        class R:
            returncode = poster_rc
            stdout = "record-uuid" if poster_rc == 0 else ""
            stderr = "" if poster_rc == 0 else "ERROR: role must be one of"
        return R()


def joined(poster: list[str]) -> str:
    return " ".join(poster)


def record_type(cmd: list[str]) -> str:
    return cmd[cmd.index("--record-type") + 1]


def tags_of(cmd: list[str]) -> list[str]:
    return json.loads(cmd[cmd.index("--tags") + 1])


def role_of(cmd: list[str]) -> str:
    return cmd[cmd.index("-r") + 1]


def test_filed_records_use_the_canonical_role():
    """'DBA' is rejected by post-agent-record.py, so the record would never
    be filed and the guard would fail silently — the exact defect that
    disabled four sibling wrappers from 2026-10-05."""
    assert WRAP.RECORD_ROLE == "dba"


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    sf = tmp_path / "state.json"
    monkeypatch.setattr(WRAP, "STATE", sf)
    return sf


def test_first_green_run_commissions(state_file, monkeypatch):
    fake = FakeRuns(0, payload())
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1
    cmd = fake.posters[0]
    assert record_type(cmd) == "report"
    assert role_of(cmd) == "dba"
    assert "commissioning" in joined(cmd)
    assert "GREEN" in joined(cmd)
    assert "series:pg-wal-guard" in tags_of(cmd)
    st = json.loads(state_file.read_text())
    assert st["ever_green"] is True
    assert st["archiver"]["archived_count"] == 0


def test_same_week_green_is_silent(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": True,
         "last_green_recorded": WRAP._iso_week(WRAP._utcnow())}))
    fake = FakeRuns(0, payload())
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert fake.posters == []


def test_new_week_green_heartbeats(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": True, "last_green_recorded": "2020-W01"}))
    fake = FakeRuns(0, payload())
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1
    assert "heartbeat" in joined(fake.posters[0])
    assert record_type(fake.posters[0]) == "report"


def test_findings_file_an_incident_with_evidence(state_file, monkeypatch):
    fake = FakeRuns(1, CRITICAL_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    cmd = fake.posters[0]
    assert record_type(cmd) == "inspection"
    assert role_of(cmd) == "dba"
    assert "CRITICAL" in joined(cmd)
    body = cmd[cmd.index("-c") + 1]
    # The numbers must be in the alert, not only in the checker output.
    assert "pg_wal is 156.0 GB (critical threshold 60.0 GB)" in body
    assert "archive_mode=off" in body
    assert "156.0 GB" in body and ".ready backlog" in body
    assert any(t in tags_of(cmd) for t in ("to:dba", "to:devops"))
    assert "type:incident" in tags_of(cmd)
    st = json.loads(state_file.read_text())
    assert st["last_drift_recorded"] == WRAP._utcnow().strftime("%Y-%m-%d")
    assert st["last_alerted_tier"] == "critical"
    assert st["ever_green"] is False


def test_findings_suppressed_same_tier_same_day(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": False,
         "last_drift_recorded": WRAP._utcnow().strftime("%Y-%m-%d"),
         "last_alerted_tier": "critical"}))
    fake = FakeRuns(1, CRITICAL_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert fake.posters == []


def test_tier_escalation_files_immediately_despite_suppression(
        state_file, monkeypatch):
    """A problem already recorded today as a warning that becomes critical
    must not wait for tomorrow's suppression window to clear."""
    state_file.write_text(json.dumps(
        {"ever_green": False,
         "last_drift_recorded": WRAP._utcnow().strftime("%Y-%m-%d"),
         "last_alerted_tier": "warn"}))
    fake = FakeRuns(1, CRITICAL_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1
    assert "CRITICAL" in joined(fake.posters[0])


def test_warn_tier_does_not_escape_suppression(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": False,
         "last_drift_recorded": WRAP._utcnow().strftime("%Y-%m-%d"),
         "last_alerted_tier": "warn"}))
    fake = FakeRuns(1, WARN_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert fake.posters == []


def test_suppressed_run_still_advances_the_archiver_snapshot(
        state_file, monkeypatch):
    """Otherwise a suppressed day would blind the progress check."""
    state_file.write_text(json.dumps(
        {"ever_green": False,
         "last_drift_recorded": WRAP._utcnow().strftime("%Y-%m-%d"),
         "last_alerted_tier": "critical",
         "archiver": {"archived_count": 1}}))
    fake = FakeRuns(1, payload(ok=False, tier="critical", findings=[FINDING],
                               measurement=measurement(wal_bytes=156 * GIB,
                                                       archived_count=99)))
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    st = json.loads(state_file.read_text())
    assert st["archiver"]["archived_count"] == 99


def test_stalled_archiver_is_folded_into_the_record(state_file, monkeypatch):
    """The cross-run check: archiving enabled with a target, counter flat."""
    state_file.write_text(json.dumps(
        {"ever_green": True, "last_green_recorded": "2020-W01",
         "archiver": {"archived_count": 400, "ready": 900}}))
    fake = FakeRuns(0, payload(measurement=measurement(
        archive_mode="on", archive_command="/bin/true", ready=900,
        archived_count=400)))
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1
    assert record_type(fake.posters[0]) == "inspection"
    assert "archiver_stall" in joined(fake.posters[0])


def test_recovery_after_findings(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": False, "last_drift_recorded": "2026-10-01",
         "last_alerted_tier": "critical"}))
    fake = FakeRuns(0, payload())
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1
    assert "recovery" in joined(fake.posters[0])


def test_recovery_clears_the_alerted_tier(state_file, monkeypatch):
    state_file.write_text(json.dumps(
        {"ever_green": False, "last_drift_recorded": "2026-10-01",
         "last_alerted_tier": "critical"}))
    fake = FakeRuns(0, payload())
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    st = json.loads(state_file.read_text())
    assert st["last_alerted_tier"] is None
    assert st["ever_green"] is True


def test_refused_green_record_does_not_consume_the_heartbeat(
        state_file, monkeypatch):
    """If the record is refused, the green it witnesses does not exist yet.
    Advancing the week here would silence the guard until next week while it
    looked healthy — how four sibling wrappers went dark on 2026-10-05."""
    fake = FakeRuns(0, payload(), poster_rc=2)
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 2
    assert len(fake.posters) == 1
    st = json.loads(state_file.read_text())
    assert st.get("ever_green") is not True
    assert "last_green_recorded" not in st
    # the archiver snapshot still advances, so progress tracking survives
    assert st["archiver"]["at"]


def test_retry_after_refused_green_record_files_again(state_file, monkeypatch):
    refused = FakeRuns(0, payload(), poster_rc=2)
    monkeypatch.setattr(WRAP.subprocess, "run", refused)
    assert WRAP.main() == 2
    accepted = FakeRuns(0, payload())
    monkeypatch.setattr(WRAP.subprocess, "run", accepted)
    assert WRAP.main() == 0
    assert len(accepted.posters) == 1
    assert "commissioning" in joined(accepted.posters[0])


def test_refused_findings_record_does_not_suppress_the_retry(
        state_file, monkeypatch):
    """A refused incident must not start the daily suppression window: that
    would swallow the retry and the finding would never reach anyone."""
    refused = FakeRuns(1, CRITICAL_PAYLOAD, poster_rc=2)
    monkeypatch.setattr(WRAP.subprocess, "run", refused)
    assert WRAP.main() == 2
    st = json.loads(state_file.read_text())
    assert st.get("last_drift_recorded") != WRAP._utcnow().strftime("%Y-%m-%d")
    assert st.get("last_alerted_tier") != "critical"
    retry = FakeRuns(1, CRITICAL_PAYLOAD)
    monkeypatch.setattr(WRAP.subprocess, "run", retry)
    assert WRAP.main() == 0
    assert len(retry.posters) == 1
    assert "CRITICAL" in joined(retry.posters[0])


def test_tool_error_files_nothing(state_file, monkeypatch):
    fake = FakeRuns(2, json.dumps({"ok": False,
                                   "tool_error": "psql exited 2: no server"}))
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 2
    assert fake.posters == []
    assert not state_file.exists()


def test_unparseable_checker_output_is_a_tool_error(state_file, monkeypatch):
    fake = FakeRuns(0, "not json")
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 2
    assert fake.posters == []


def test_unknown_checker_exit_is_a_tool_error(state_file, monkeypatch):
    fake = FakeRuns(7, payload())
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 2
    assert fake.posters == []


def test_corrupt_state_file_does_not_crash_the_guard(state_file, monkeypatch):
    state_file.write_text("{not json")
    fake = FakeRuns(0, payload())
    monkeypatch.setattr(WRAP.subprocess, "run", fake)
    assert WRAP.main() == 0
    assert len(fake.posters) == 1


# ---------------------------------------------------------------------------
# Checker: live measurement path (faked psql)
# ---------------------------------------------------------------------------

def test_measure_invokes_psql_with_dsn_and_parses(monkeypatch):
    seen = {}

    def fake(cmd, **kwargs):
        seen["cmd"] = [str(c) for c in cmd]

        class R:
            returncode = 0
            stdout = ROW + "\n"
            stderr = ""
        return R()

    monkeypatch.setattr(CHECK.subprocess, "run", fake)
    m = CHECK.measure(dsn="postgresql://u:p@h:5432/db", psql="psql")
    assert m["ready"] == 12
    assert "postgresql://u:p@h:5432/db" in seen["cmd"]
    assert "ON_ERROR_STOP=1" in seen["cmd"]
    assert "pg_ls_archive_statusdir" in seen["cmd"][-1]


def test_measure_raises_on_psql_failure(monkeypatch):
    def fake(cmd, **kwargs):
        class R:
            returncode = 2
            stdout = ""
            stderr = "connection refused"
        return R()

    monkeypatch.setattr(CHECK.subprocess, "run", fake)
    with pytest.raises(RuntimeError) as exc:
        CHECK.measure()
    assert "connection refused" in str(exc.value)


def test_measure_raises_when_psql_is_missing(monkeypatch):
    def fake(cmd, **kwargs):
        raise FileNotFoundError("psql: command not found")

    monkeypatch.setattr(CHECK.subprocess, "run", fake)
    with pytest.raises(RuntimeError):
        CHECK.measure()


def test_checker_main_reports_tool_error_as_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(CHECK, "measure",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("no server")))
    assert CHECK.main() == 2
    assert "tool_error" in capsys.readouterr().out


def test_checker_main_exit_1_with_findings(monkeypatch, capsys):
    monkeypatch.setattr(CHECK, "measure", lambda *a, **k: measurement(
        wal_bytes=30 * GIB))
    monkeypatch.setattr(CHECK, "measure_disk", lambda *a, **k: disk())
    assert CHECK.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["tier"] == "warn"


def test_checker_main_exit_0_when_healthy(monkeypatch, capsys):
    monkeypatch.setattr(CHECK, "measure", lambda *a, **k: measurement())
    monkeypatch.setattr(CHECK, "measure_disk", lambda *a, **k: disk())
    assert CHECK.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["tier"] is None


def test_threshold_env_overrides_are_honoured(monkeypatch):
    monkeypatch.setenv("PG_WAL_SIZE_WARN_GIB", "1.5")
    monkeypatch.setenv("PG_WAL_READY_CRIT", "5")
    th = CHECK.thresholds()
    assert th["wal_warn_bytes"] == int(1.5 * GIB)
    assert th["ready_crit"] == 5


def test_checker_and_wrapper_agree_on_the_incident_config(
        state_file, monkeypatch):
    """End-to-end on the real incident numbers: the checker calls it
    critical and the wrapper would file it as an incident."""
    m = measurement(wal_bytes=156 * GIB, ready=10007, archive_mode="on")
    found = CHECK.classify(m, disk(free_gib=17.0), TH)
    assert CHECK.overall_tier(found) == "critical"
    monkeypatch.setattr(WRAP.subprocess, "run",
                        FakeRuns(1, json.dumps({"ok": False,
                                                "tier": "critical",
                                                "measurement": m,
                                                "disk": disk(free_gib=17.0),
                                                "thresholds": TH,
                                                "findings": found})))
    assert WRAP.main() == 0
    st = json.loads(state_file.read_text())
    assert st["last_alerted_tier"] == "critical"


def test_state_dir_env_is_respected(monkeypatch, tmp_path):
    target = tmp_path / "custom" / "state.json"
    monkeypatch.setenv("PG_WAL_GUARD_STATE", str(target))
    reloaded = _load("pg_wal_guard_wrap_env", REPO / "bin" /
                     "pg_wal_guard_wrap.py")
    assert str(reloaded.STATE) == str(target)
    assert os.environ["PG_WAL_GUARD_STATE"] == str(target)
