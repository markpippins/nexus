#!/usr/bin/env python3
"""
calendar/emitter.py — Calendar emitter for barium standby node.

Emits periodic heartbeats to the NATS-backed heartbeat registry so the
fleet monitor knows this node is alive. Runs as a lightweight sidecar
on standby nodes (barium, thallium, etc.).
"""

import asyncio
import datetime
import json
import os
import signal
import sys
from typing import Any, Dict

import nats
from nats.js import JetStreamContext

# ── Configuration ──────────────────────────────────────────────────
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
HEARTBEAT_REGISTRY_URL = os.getenv("HEARTBEAT_REGISTRY_URL", "")
NODE_HOSTNAME = os.getenv("NODE_HOSTNAME", "barium")
NODE_IP = os.getenv("NODE_IP", "192.168.1.212")
NODE_ENV = os.getenv("NODE_ENV", "Development")
NODE_ROLE = os.getenv("NODE_ROLE", "standby")
NODE_SERVICES = os.getenv("NODE_SERVICES", "calendar-emitter").split(",")

# ── Logging ────────────────────────────────────────────────────────
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
log = logging.getLogger("calendar.emitter")


class CalendarEmitter:
    """Emits periodic heartbeats to the NATS-backed heartbeat registry."""

    def __init__(self):
        self.nc = None
        self.js: JetStreamContext = None
        self._shutdown = asyncio.Event()

    async def connect(self) -> None:
        self.nc = await nats.connect(os.getenv("NATS_URL", "nats://localhost:4222"))
        self.js = self.nc.jetstream()
        log.info("Connected to NATS at %s", os.getenv("NATS_URL", "nats://localhost:4222"))

    async def emit_heartbeat(self) -> None:
        """Emit a single heartbeat to the registry."""
        payload = {
            "hostname": NODE_HOSTNAME,
            "ip_address": NODE_IP,
            "environment": NODE_ENV,
            "role": NODE_ROLE,
            "services": NODE_SERVICES,
            "status": "ACTIVE",
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "uptime_seconds": int(time.time() - self._start_time),
        }
        try:
            await self.js.publish(
                "nexus.heartbeat.registry",
                json.dumps(payload).encode(),
                headers={"Nats-Expected-Stream": "nexus.heartbeat", "Nats-Expected-Subject": "nexus.heartbeat.registry"},
            )
            log.debug("Heartbeat emitted for %s", NODE_HOSTNAME)
        except Exception as e:
            log.warning("Failed to emit heartbeat: %s", e)

    async def run(self) -> None:
        await self.connect()
        self._start_time = time.time()

        # Initial heartbeat
        await self.emit_heartbeat()

        # Periodic heartbeat every 30 seconds
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=30.0)
                break
            except asyncio.TimeoutError:
                await self.emit_heartbeat()

    async def shutdown(self) -> None:
        self._shutdown.set()
        if self.nc:
            await self.nc.drain()
            await self.nc.close()


async def main() -> None:
    emitter = CalendarEmitter()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, emitter.shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda s, f: emitter.shutdown())

    await emitter.run()


if __name__ == "__main__":
    import time
    asyncio.run(main())