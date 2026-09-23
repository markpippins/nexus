# Moleculer — Port Map (mirror of the ratified convergence artifact)

> **Status: RATIFIED** — mirror side of the architect-owned port map (Ruling 4,
> decision `8ae4761b`; operator endorsement 2026-09-22). The canonical table
> lives in `jvm/ARCHITECTURE.md` (Port Allocation); this file is the moleculer
> distribution's view of the same ownership map. The registry
> (terrain-mcp / terrain :8084) is the authoritative "what runs where" surface.
>
> **Moleculer tier alignment (Rule 5):** moleculer services register on the
> NATS mesh (`:4222`) in their own namespace and expose REST via moleculer-web
> on their deployment port. They do NOT claim JVM-band ports (8080-8099), the
> shared service-registry 8085, or any TS-owned surface.

## Moleculer-deployed services (REST via moleculer-web)

| Port | Service | Namespace | Legacy counterpart | Registry status |
|------|---------|-----------|--------------------|-----------------|
| 4050 | `api` gateway + `google-search` (`simpleSearch`, `health`) | `search` | `broker-gateway :8081` op-dispatch (`googlePublicSearch`); search-service embedded, no HTTP surface of its own | ONLINE — live per M1 (slice 1 parity proven 2026-09-11) |
| 4060 | `solscript` facade (resolution-domain evaluation API) | `solscript` | JVM `@nexus/solscript` (no direct legacy HTTP twin) | OFFLINE (systemd `moleculer-solscript.service`; built, not running) |
| 4080 | `nexus-broker` worker tier (`worker.harness`, `worker.pty`, `worker.execution`, `keychain-snapshot`) | `broker` | `harness-srv :3420`, `execution-srv :3110`, `pty-srv :3121` (**retired M3** → `worker.pty-transport` WS `:3130`) | ONLINE — partial cutovers |
| 4114 | `voyager` (canary twin of `typescript/voyager-srv`) | `voyager` | `typescript/voyager-srv :3114` (read-only filesystem/entity voyager; contract pinned to incumbent `openapi.yaml`) | CANARY — merged (PR #453), canary-diffed byte-identical, **not cut over / not deployed** |

## Shared infrastructure (NOT moleculer-owned — do not claim)

| Port | Owner | Why moleculer must not touch it |
|------|-------|--------------------------------|
| 8085 | service-registry (JVM) | shared infrastructure across distributions (Ruling 3) |
| 8081 | broker-gateway (JVM) | live until its moleculer twin reaches zero-traffic cutover; M1 owns "zero SEARCH traffic" not "zero broker traffic" |
| 8082 | nexus-control-edge (TS) | TS-owned |
| 8092 | thallium-search (JVM nexus-core lane on **thallium** `192.168.1.82:8092`, `/search/api`) | thallium hosts JVM nexus-core (Ruling 1); helium JVM retired |
| 8098 | peb-kernel (Python) | Python owns 8098; JVM peb-bootstrap defers to 8099 |

## NATS mesh

| Component | Address |
|-----------|---------|
| NATS broker | `nats://localhost:4222` (services default via `NATS_URL` env, `nats://localhost:4222`) |
| Namespaces | one per service family: `search`, `solscript`, `broker`, `voyager` |

## Freeze discipline

- The M1 cutover manifest (`M1-SERVICE-MANIFEST.md`, frozen 2026-09-09) is the
  single inventory M1 gates cutover against; this port map records present
  ownership, not cutover authorization.
- Any port change ratified in `jvm/ARCHITECTURE.md` must be mirrored here and
  in the owning service's config + launch units in the same change; the
  terrain registry entry follows at the next registration heartbeat.
- Canary/deployment ports (4114) are the exception to one-live-authority: a
  canary twin may co-listen on its 41xx twin while its incumbent stays live,
  and must be removed from the map when cutover completes (dead routes die).