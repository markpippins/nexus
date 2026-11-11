"""Gate the LAN bind of the nexus-console dev server.

`nexus-console.service` runs `npm start`, i.e. `ng serve`. The serve target in
`angular.json` set only `port`, so Angular's dev-server fell back to its
`localhost` default -- which resolves to `::1` on this fleet. The console was
therefore reachable only from the machine running it, while the console's
intended same-LAN access requires an explicit all-interface bind. The tracked
conduit-ui is a Vite app and does not declare a host; the gitignored
atlas-ui/application-host checkouts are standalone repos, not tracked in-repo
precedent.

The unit file lives outside the repo (`~/.config/systemd/user/`), so
`angular.json` is the only tracked lever. Without this gate the bind can
silently regress to loopback the next time someone regenerates the serve block,
and the failure is invisible locally: the console keeps working on the machine
that serves it.
"""
from __future__ import annotations

import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
PROJECT = REPO / "angular" / "nexus-console"
ANGULAR_JSON = PROJECT / "angular.json"
PROXY_CONF = PROJECT / "proxy.conf.json"

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _serve():
    config = json.loads(ANGULAR_JSON.read_text(encoding="utf-8"))
    return config["projects"]["app"]["architect"]["serve"]


class NexusConsoleLanBindTests(unittest.TestCase):
    def test_serve_target_exists(self):
        self.assertTrue(ANGULAR_JSON.is_file(), f"missing {ANGULAR_JSON}")
        self.assertIn("options", _serve())

    def test_serve_binds_all_interfaces(self):
        host = _serve()["options"].get("host")
        self.assertIsNotNone(
            host,
            "serve.options.host is unset -- Angular's dev-server then binds "
            "localhost (::1 here) and nexus-console becomes local-only again",
        )
        self.assertEqual(host, "0.0.0.0", f"expected the LAN bind, got {host!r}")
        self.assertNotIn(host, LOOPBACK_HOSTS)

    def test_no_configuration_narrows_the_bind_back_to_loopback(self):
        """A per-configuration `host` would silently win over the default."""
        for name, block in (_serve().get("configurations") or {}).items():
            host = block.get("host")
            if host is None:
                continue
            self.assertNotIn(
                host,
                LOOPBACK_HOSTS,
                f"serve configuration {name!r} narrows the bind back to loopback",
            )

    def test_port_is_unchanged(self):
        """4200 is what start-nexus-uis.sh and the systemd unit expect."""
        self.assertEqual(_serve()["options"].get("port"), 4200)

    def test_dev_proxy_still_wired(self):
        """A wide bind is only useful if /api and /chat still reach the backend.

        The dev server proxies those paths itself, so the LAN bind inherits
        working API routing -- but only while serve.options still references
        the proxy config and that file still maps /api.
        """
        self.assertEqual(_serve()["options"].get("proxyConfig"), "proxy.conf.json")
        proxy = json.loads(PROXY_CONF.read_text(encoding="utf-8"))
        self.assertIn("/api", proxy, f"{PROXY_CONF} no longer proxies /api")


if __name__ == "__main__":
    unittest.main()
