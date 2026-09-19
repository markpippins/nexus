"""Hermetic tests: CanonicalEnvelope origin_host / origin_instance.

Pins the fleet-identity contract (R1 0d40c061, topology build-out):
  - origin fields default from NEXUS_HOST / NEXUS_INSTANCE env
  - absent env → None (absent identity is honest, never fabricated)
  - NEXUS_INSTANCE falls back to NEXUS_HOST (single-instance hosts get
    a usable, terrain-compatible instance key)
  - explicit constructor args always win over env
  - round-trip through to_dict/from_dict preserves both fields
  - from_dict of a legacy (field-absent) payload stays None, not host
  - serialization shape: keys always present (None allowed), so
    consumers can rely on the field existing

No network, no DB — env is manipulated via mock.patch.dict.
"""

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (_HERE, os.path.abspath(os.path.join(_HERE, ".."))):
    if p not in sys.path:
        sys.path.insert(0, p)

from nats_envelope.envelope import CanonicalEnvelope  # noqa: E402


def _env(**kw):
    return mock.patch.dict(os.environ, kw, clear=False)


def _no_env():
    """Remove both fleet vars to simulate an unconfigured machine."""
    return mock.patch.dict(os.environ, {}, clear=True)


def _base(**kw):
    defaults = dict(
        event_type="TestEvent",
        origin_component="cascade",
        correlation_id="corr-1",
        subject="nexus.cascade.v1.test.test_event",
        payload={"x": 1},
    )
    defaults.update(kw)
    return CanonicalEnvelope(**defaults)


class OriginDefaults(unittest.TestCase):
    def test_env_host_and_instance_flow_in(self):
        with _env(NEXUS_HOST="titanium", NEXUS_INSTANCE="titanium"):
            e = _base()
        self.assertEqual(e.origin_host, "titanium")
        self.assertEqual(e.origin_instance, "titanium")

    def test_absent_env_is_none_not_fabricated(self):
        with _no_env():
            e = _base()
        self.assertIsNone(e.origin_host)
        self.assertIsNone(e.origin_instance)

    def test_instance_falls_back_to_host(self):
        with _env(NEXUS_HOST="helium"):
            os.environ.pop("NEXUS_INSTANCE", None)
            e = _base()
        self.assertEqual(e.origin_host, "helium")
        self.assertEqual(e.origin_instance, "helium")

    def test_explicit_args_win_over_env(self):
        with _env(NEXUS_HOST="titanium", NEXUS_INSTANCE="titanium"):
            e = _base(origin_host="entropy", origin_instance="docker-cascade-1")
        self.assertEqual(e.origin_host, "entropy")
        self.assertEqual(e.origin_instance, "docker-cascade-1")


class OriginSerialization(unittest.TestCase):
    def test_round_trip_preserves_fields(self):
        with _env(NEXUS_HOST="titanium", NEXUS_INSTANCE="ti-1"):
            e = _base()
        d = e.to_dict()
        self.assertEqual(d["origin_host"], "titanium")
        self.assertEqual(d["origin_instance"], "ti-1")
        e2 = CanonicalEnvelope.from_dict(d)
        self.assertEqual(e2.origin_host, "titanium")
        self.assertEqual(e2.origin_instance, "ti-1")

    def test_keys_always_present_even_when_none(self):
        with _no_env():
            d = _base().to_dict()
        self.assertIn("origin_host", d)
        self.assertIn("origin_instance", d)
        self.assertIsNone(d["origin_host"])
        self.assertIsNone(d["origin_instance"])

    def test_legacy_payload_without_fields_stays_none(self):
        with _no_env():
            legacy = _base().to_dict()
            legacy.pop("origin_host")
            legacy.pop("origin_instance")
            e = CanonicalEnvelope.from_dict(legacy)
        self.assertIsNone(e.origin_host)
        self.assertIsNone(e.origin_instance)


if __name__ == "__main__":
    unittest.main()
