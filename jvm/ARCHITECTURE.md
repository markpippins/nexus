# JVM Platform Architecture

Inherits from: `../ARCHITECTURE.md`

## Platform Defaults

| Setting | Value |
|---------|-------|
| java.version | 21 |
| spring-boot.version | 3.5.0 |
| quarkus.version | 3.15.1 |
| helidon.version | 4.x |
| maven.version | 3.9.x |
| port.range (Spring/Quarkus) | 8080-8099 |
| port.range (Helidon) | 9090-9099 |

## Exceptions

| Project | Setting | Value | Reason |
|---------|---------|-------|--------|
| helidon/user-access-service | java.version | 17 | Helidon MP compatibility |

## Services

See parent ARCHITECTURE.md for service topology. This file defines platform-level defaults only.

## Machine Topology Doctrine (operator, 2026-09-22; amended 2026-09-22 per Ruling 1 of decision `8ae4761b`)

**One nexus distro per machine.** Titanium runs at most ONE of TypeSpec,
Moleculer, or the JVM tier at a time — as a rule, not even two. Multi-distro
experiments belong on **helium**. Moleculer is the gradual replacement target
for much of `nexus/typescript`.

**JVM conformance host = thallium (Ruling 1).** The JVM nexus-core now runs
on **thallium** `192.168.1.82:8092` — the rebuilt `nexus-core` image is
deployed there (terrain id 151, `thallium-search :8092`, ONLINE — the
moleculer search module inside the monolith mounts at `/search/api`, not
`/api/search`, to avoid collision with the broker aggregate). **Helium's
nexus-core JVM is retired** (Ruling 1); helium remains the multi-distro
experiment/container host, but no longer hosts the JVM lane. Titanium keeps
service-registry/service-broker (and the TS surfaces) but does **not** co-host
the JVM nexus-core group.

Consequences: titanium deployment work (units, ports, rollouts) must not
assume JVM co-location with TS services; the fat-jar rollout's nexus-core-*
group targets the **thallium** host (its container image), not a titanium unit
and not helium; cross-machine references (gateway defaults, registry entries)
must name their host explicitly rather than assuming localhost.

## Port Allocation

> **Status: RATIFIED — architect-owned convergence artifact** (Ruling 4 of
> decision `8ae4761b`; operator endorsement 2026-09-22). Registry evidence:
> terrain `/api/v1/runnable-services`, live-verified 2026-09-22. The registry
> (terrain-mcp / terrain :8084) is the authoritative "what runs where" surface —
> consult it before any port assignment. This table is the single point of
> truth for ownership claims; the moleculer distribution mirrors it from its
> side in `moleculer/PORT-MAP.md`.

### Frozen assignments (live, registry-confirmed)

| Port | Owner | Framework | Registry status |
|------|-------|-----------|-----------------|
| 8080 | vd-ci-jenkins | Jenkins (CI) | ONLINE — not free |
| 8081 | service-broker/broker-gateway (hosts export, file, search, shrapnel-data, social-media, tackle-registry as embedded modules) | Spring Boot | ONLINE — **search-service authority until standalone deployment (see 8094)** |
| 8082 | nexus-control-edge (AdonisJS/TS; currently the gateway's login target) | Node/TS | ONLINE — TS-owned; **NOT the JVM login authority (see 9093)** |
| 8084 | terrain (also registered as topology-server) | Spring Boot | ONLINE — the registry itself; no other service may claim 8084 |
| 8085 | service-registry | Spring Boot | ONLINE — **shared infrastructure across distributions (Ruling 3)** |
| 8090 | atlas | Spring Boot | ONLINE |
| 8091 | quarkus-broker-gateway | Quarkus | ONLINE (dev mode; own config still says 8090) |
| 8092 | thallium-search (JVM nexus-core lane on **thallium** `192.168.1.82:8092`; moleculer search mounts at `/search/api`) | Spring Boot (monolith) | ONLINE — **thallium hosts JVM nexus-core (Ruling 1); helium JVM retired** |
| 8098 | peb-kernel (`python3 -m peb_kernel.main`) | Python | ONLINE — **Python owns 8098; JVM peb-bootstrap must NOT claim it (see 8099)** |
| 9093 | helidon/user-access-service | Helidon MP | ONLINE — **the JVM login authority; gateway `login-service` default must point here** |
| 9095-9097 | ballerina-ci-gateway / sonar-sync / jenkins-sync | Java (tooling) | ONLINE |

Free in the Spring/Quarkus band on titanium: 8083, 8086-8089, 8093-8097, 8099 (8092 is thallium-remote, not free on titanium).

### Ratified authority entries (decision `8ae4761b`; resolves the F1/F2 hazards)

| Port | Authority | Rationale / resolution |
|------|-----------|------------------------|
| 8084 | terrain (registry) only | **F2-search resolved:** the gateway's `search-service` default must NOT point at 8084; search authority is embedded `:8081` until the standalone deployment takes `8094` |
| 9093 | helidon user-access-service = JVM login surface | **F2-login resolved:** the gateway's `login-service` default must point here, not at TS-owned `8082` (nexus-control-edge owns 8082) |
| 8094 | spring/service-broker/search-service (standalone, **staged — after fat-jar rollout**, not live) | gives the JVM search service its own port; today it runs embedded in broker-gateway `:8081` |
| 8099 | spring/peb-kernel/peb-bootstrap (JVM peb, **dormant until deployed**) | **F2-peb-bootstrap resolved:** 8098 is the live Python peb-kernel per the registry; the JVM bootstrap's `application.yml` claim on 8098 is a defect and must move to 8099 before activation |

### Rules

1. New JVM services take the lowest free port in their framework's band; never
   target a port owned by another service or language family.
2. The Quarkus gateway's `external.services.urls` defaults must point only at
   ports in this table. A default pointing at a foreign-owned or empty port is
   a defect — all three known defects are resolved above: `search-service →
   8084` (→ 8081/8094), `login-service → 8082` (→ 9093), and `user-service →
   8083` (nothing registered, nothing listening — must point at 9093 while the
   gateway searches for its own user surface).
3. Cross-language ownership is real: the Python peb-kernel owns 8098, the TS
   nexus-control-edge owns 8082, the JVM nexus-core lane is on thallium 8092.
   Each distribution's defaults must point only at its own authorities; the
   moleculer tier mirrors the same entries in `moleculer/PORT-MAP.md`.
4. Any port change ratified here must be mirrored in the owning service's
   config and its launch units in the same change; the registry entry follows
   at the next registration heartbeat.
5. **Moleculer tier alignment:** moleculer services register on the NATS mesh
   (`:4222`) in their namespace and expose REST via moleculer-web on their
   port; they do NOT claim JVM-band ports (8080-8099) or the shared 8085.
   Moleculer ports (4050/4060/4080; canary twins
   4100/4106/4109/4114/4150/4160/4170/4420/4501) are
   recorded in `moleculer/PORT-MAP.md`. On titanium these ports are asserted
   UNBOUND by `bin/assert_moleculer_ports.sh` (moleculer does not run locally;
   the candidate-tier lane is containerized on host :14080) — the script is
   the enforcement of that posture, with a documented time-boxed exception
   path for canary runs.
