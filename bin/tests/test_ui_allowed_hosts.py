"""Gate the dev-server host-header allowlist for the LAN-accessible UIs.

Every UI fleet member binds 0.0.0.0 so a browser on another machine can reach
it. Vite (which also backs Angular's dev server) then rejects any request whose
Host header it does not recognise:

    Blocked request. This host ("titanium") is not allowed.

The port is open, `curl 127.0.0.1:<port>` returns 200, and the fleet status
script reports UP -- so the failure is invisible from the server and looks like
"the UI is down" from the browser.

Two mechanisms exist, and they are not interchangeable:

* Vite apps -- `server.allowedHosts`. The hosts come from the environment
  (`NEXUS_UI_ALLOWED_HOSTS`) so no machine name is committed. Vite's own
  `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS` variable cannot be used on its own:
  it is only consulted when `server.allowedHosts` is already an array.
* Angular apps -- the dev-server does NOT propagate that environment variable
  (verified: an empty array, a benign array, and an env-supplied host all stayed
  blocked), so they declare `allowedHosts: true` (or `disableHostCheck: true` on
  the legacy devkit builder). This is a dev server on a trusted LAN, not a
  production binding.

Apps whose sources live outside this repo (embedded checkouts) cannot be gated
here; this test only covers what is committed.
"""
from __future__ import annotations

import json
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
ANGULAR = REPO / "angular"


def _serve_options(angular_json: pathlib.Path) -> dict | None:
    try:
        config = json.loads(angular_json.read_text(encoding="utf-8"))
        project = next(iter(config["projects"].values()))
        return project["architect"]["serve"].get("options", {})
    except Exception:
        return None


class UiAllowedHostsTests(unittest.TestCase):
    def test_every_committed_angular_app_allows_the_lan_host(self):
        checked = 0
        for angular_json in sorted(ANGULAR.glob("*/angular.json")):
            opts = _serve_options(angular_json)
            if opts is None:
                continue
            checked += 1
            allowed = opts.get("allowedHosts")
            legacy = opts.get("disableHostCheck")
            self.assertTrue(
                allowed is True or legacy is True or isinstance(allowed, list),
                f"{angular_json.parent.name}: serve options declare neither "
                f"allowedHosts nor disableHostCheck, so a LAN hostname gets "
                f"'Blocked request' while the port is open",
            )
        self.assertGreater(checked, 0, "no angular apps found -- wrong repo root?")

    def test_every_committed_vite_app_reads_hosts_from_the_environment(self):
        configs = sorted(ANGULAR.glob("*/vite.config.ts"))
        self.assertGreater(len(configs), 0, "no vite configs found")
        for cfg in configs:
            text = cfg.read_text(encoding="utf-8")
            self.assertIn(
                "NEXUS_UI_ALLOWED_HOSTS",
                text,
                f"{cfg.parent.name}: vite server.allowedHosts is not wired to the "
                f"environment, so LAN hostnames are rejected",
            )
            self.assertNotIn(
                "titanium", text,
                f"{cfg.parent.name}: machine-specific hostname committed to config",
            )

    def test_no_machine_name_is_committed_in_angular_serve_options(self):
        for angular_json in sorted(ANGULAR.glob("*/angular.json")):
            text = angular_json.read_text(encoding="utf-8")
            self.assertNotIn(
                "titanium",
                text,
                f"{angular_json.parent.name}: machine-specific hostname committed",
            )

    def test_lan_angular_apps_bind_all_interfaces(self):
        """Angular's dev server defaults to `localhost`, which is ::1 here.

        That binds IPv6 loopback only: the port is invisible on 127.0.0.1 and
        unreachable from the LAN, which is how cascade-ui (4203) and nebula-ui
        (4210) looked "down" for months while their units reported active.
        """
        declared = {}
        for app in ("cascade-ui", "nexus-console"):
            opts = _serve_options(ANGULAR / app / "angular.json")
            self.assertIsNotNone(opts, f"{app}: no serve options")
            declared[app] = opts.get("host")
        for app, host in declared.items():
            self.assertEqual(
                host,
                "0.0.0.0",
                f"{app}: serve.options.host is {host!r}; Angular then binds "
                f"localhost (::1) and the port is LAN-invisible",
            )


if __name__ == "__main__":
    unittest.main()
