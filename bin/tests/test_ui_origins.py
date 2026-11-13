"""Gate the iframe origins in nexus-console.

The console embeds its sibling UIs in iframes, one port each. That URL map
hardcoded `http://localhost:<port>`, which is only correct when the browser runs
on the machine serving the console: for any remote browser `localhost` resolves
to the viewer's own computer, so the frame silently loads nothing (or an
unrelated app). `uiOriginFor()` derives the host from the embedding page.

Regression mode observed in the wild: console reachable at 192.168.1.18:4200,
every embedded UI blank, nothing in the console's own logs.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "angular" / "nexus-console" / "src"
APP = SRC / "app.component.ts"
HELPER = SRC / "ui-origins.ts"
SELECTOR = SRC / "components" / "external-site-selector" / "external-site-selector.component.ts"


class IframeOriginTests(unittest.TestCase):
    def test_helper_derives_host_from_the_embedding_page(self):
        self.assertTrue(HELPER.is_file(), f"missing {HELPER}")
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn("export function uiOriginFor", text)
        self.assertIn("window.location", text)
        self.assertIn("hostname", text)

    def test_embedded_ui_urls_do_not_hardcode_localhost(self):
        text = APP.read_text(encoding="utf-8")
        start = text.index("viewModeUrls: Record<string, string> = {")
        end = text.index("};", start)
        block = text[start:end]
        self.assertNotRegex(
            block,
            r"http://localhost:\d+",
            "embedded UI URLs must derive their host, not hardcode localhost",
        )
        derived = re.findall(r"uiOriginFor\((\d+)\)", block)
        self.assertGreaterEqual(
            len(derived), 20, f"only {len(derived)} embedded UIs derive a host"
        )

    def test_tackle_ui_and_external_selector_derive_host(self):
        app = APP.read_text(encoding="utf-8")
        self.assertNotIn("tackleUiUrl = 'http://localhost", app)
        self.assertIn("uiOriginFor(4202)", app)
        sel = SELECTOR.read_text(encoding="utf-8")
        self.assertIn("uiOriginFor(3003)", sel)
        self.assertNotIn("'http://localhost:3003'", sel)


if __name__ == "__main__":
    unittest.main()
