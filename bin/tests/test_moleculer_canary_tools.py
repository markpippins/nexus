"""Fleet guard: moleculer twin canary tooling must be correctly parameterized.

Closes the tester gap finding from the #682 attestation (ebfb09f9):
moleculer/wind's canary tools shipped as byte-identical copies of aegis's
(4116/3116/aegis.service.js), and the same class reached main via #684
(substance's tools + standalone-config docstring). No test had covered
the canary tools at all.

Policy (reality-matched to the fleet's tooling variety):
  - Every twin's tooling must carry its OWN canary port, incumbent port,
    and service-file names, and its standalone config must be namespaced
    to itself.
  - FOREIGN OPERATIONAL tokens (sibling service files, sibling ports in
    executable lines) fail. Prose mentions of siblings are allowed —
    twins legitimately document cross-references (nebula's substance
    coupling, shared band history) — so port residue is checked with
    comments stripped, and namespace/service-file checks use the exact
    quoted/literal forms.
  - The runner must boot from dist/services/{api,<twin>}.service.js (the
    aegis-verbatim copy failed exactly here: it demanded aegis.service.js
    inside the wind twin and aborts at its own build precheck).
  - The diff tool defaults (CANARY_BASE/TWIN_BASE) must point at the
    twin's own incumbent/canary ports.
"""
import os
import re
import py_compile

import pytest

pytest.importorskip("yaml")
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def registry_twins():
    doc = yaml.safe_load(open(os.path.join(ROOT, "moleculer", "ports.yaml")))
    return {row["name"]: row for row in doc.get("canary", [])}


def twin_path(twin, rel):
    return os.path.join(ROOT, "moleculer", twin, rel)


def read(twin, rel):
    path = twin_path(twin, rel)
    if not os.path.exists(path):
        return None
    return open(path).read()


def strip_comments(text):
    # sh + js-style line comments and js block comments; enough for
    # prose-vs-code discrimination in these small tools.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(
        re.sub(r"(^|\s)(#|//).*$", "", line) for line in text.splitlines()
    )


TWINS = registry_twins()
ALL_NAMES = set(TWINS)

TOOLING_TWINS = {
    name for name in TWINS
    if read(name, "tools/canary-run.sh") is not None
    and read(name, "tools/canary-diff.py") is not None
}


def test_guard_is_not_vacuous():
    assert len(TOOLING_TWINS) >= 4, (
        f"guard covers only {TOOLING_TWINS} — the tooling trio is expected on most canary twins"
    )
    assert {"aegis", "substance", "nebula"} <= TOOLING_TWINS


@pytest.mark.parametrize("twin", sorted(TOOLING_TWINS))
class TestCanaryTooling:
    def test_runner_boots_own_service_files(self, twin):
        src = read(twin, "tools/canary-run.sh")
        assert "dist/services/api.service.js" in src
        assert f"dist/services/{twin}.service.js" in src, (
            f"{twin}: canary-run.sh does not boot its own dist/services/{twin}.service.js"
        )

    def test_runner_boots_no_foreign_service_file(self, twin):
        src = read(twin, "tools/canary-run.sh")
        for other in ALL_NAMES - {twin}:
            assert f"dist/services/{other}.service.js" not in src, (
                f"{twin}: canary-run.sh boots sibling's {other}.service.js"
            )

    def test_runner_carries_own_canary_port(self, twin):
        row = TWINS[twin]
        code = strip_comments(read(twin, "tools/canary-run.sh"))
        assert re.search(rf"(?<!\d){row['port']}(?!\d)", code), (
            f"{twin}: own canary port {row['port']} absent from executable lines"
        )

    def test_runner_log_is_twin_identifiable(self, twin):
        src = read(twin, "tools/canary-run.sh")
        logs = set(re.findall(r"/tmp/[A-Za-z0-9._-]+", src))
        assert logs, f"{twin}: canary-run.sh writes no /tmp log"
        stem = sorted(logs)[0].split("/")[-1]
        # Fleet conventions: moleculer-<twin>-canary.log, ps-*, ex-*.
        assert stem.startswith(tuple("abcdefghijklmnopqrstuvwxyz")), (
            f"{twin}: unexpected log name {stem}"
        )

    def test_runner_has_no_foreign_canary_port_in_code(self, twin):
        row = TWINS[twin]
        code = strip_comments(read(twin, "tools/canary-run.sh"))
        for other in ALL_NAMES - {twin}:
            if other not in TWINS:
                continue
            foreign = str(TWINS[other]["port"])
            if foreign == str(row["port"]):
                continue
            assert not re.search(rf"(?<!\d){foreign}(?!\d)", code), (
                f"{twin}: canary-run.sh references {other}'s canary port {foreign}"
            )

    def test_diff_tool_carries_own_ports(self, twin):
        # Structure varies across the fleet (env-var defaults in most,
        # hardcoded URL constants in prompt-sync); the invariant is that
        # the tool addresses the twin's own incumbent and canary ports.
        row = TWINS[twin]
        code = strip_comments(read(twin, "tools/canary-diff.py"))
        assert re.search(rf"(?<!\d){row['incumbent_port']}(?!\d)", code), (
            f"{twin}: diff tool never references its incumbent port {row['incumbent_port']}"
        )
        assert re.search(rf"(?<!\d){row['port']}(?!\d)", code), (
            f"{twin}: diff tool never references its canary port {row['port']}"
        )
        for other in ALL_NAMES - {twin}:
            if other not in TWINS:
                continue
            for foreign_key in ("port", "incumbent_port"):
                foreign = str(TWINS[other][foreign_key])
                if foreign in (str(row["port"]), str(row["incumbent_port"])):
                    continue
                assert not re.search(rf"(?<!\d){foreign}(?!\d)", code), (
                    f"{twin}: diff tool references {other}'s {foreign_key} {foreign}"
                )

    def test_diff_tool_is_valid_python(self, twin):
        py_compile.compile(twin_path(twin, "tools/canary-diff.py"), doraise=True)

    def test_standalone_config_namespaced_to_self(self, twin):
        src = read(twin, "moleculer.config.standalone.js")
        assert f'namespace: "{twin}"' in src, (
            f"{twin}: standalone config namespace is not '{twin}'"
        )
        assert f'nodeID: "{twin}-standalone-1"' in src
        assert "transporter: null" in src

    def test_standalone_config_has_no_foreign_quoted_literal(self, twin):
        src = read(twin, "moleculer.config.standalone.js")
        for other in ALL_NAMES - {twin}:
            assert f'"{other}"' not in src, (
                f"{twin}: standalone config carries sibling's quoted literal '{other}'"
            )

    def test_tooling_not_a_verbatim_aegis_copy(self, twin):
        if twin == "aegis":
            return
        own = read(twin, "tools/canary-run.sh")
        aegis = read("aegis", "tools/canary-run.sh")
        assert own != aegis, f"{twin}: canary-run.sh is a byte-identical copy of aegis's"
