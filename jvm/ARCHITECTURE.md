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

## Machine Topology Doctrine (operator, 2026-09-22)

**One nexus distro per machine.** Titanium runs at most ONE of TypeSpec,
Moleculer, or the JVM tier at a time — as a rule, not even two. Multi-distro
experiments belong on **helium**. Moleculer is the gradual replacement target
for much of `nexus/typescript`. Nexus Core JVM runs on helium (container,
probe-watched from titanium); titanium's 8092 is freed.

Consequences: titanium deployment work (units, ports, rollouts) must not
assume JVM co-location with TS services; the fat-jar rollout's nexus-core-*
group targets the helium container image, not a titanium unit; cross-machine
references (gateway defaults, registry entries) must name their host
explicitly rather than assuming localhost.

## Port Allocation

> **Status: DRAFT — pending roundtable ratification** (decision thread in the
> Assembly discussions forum; architect binds per I2). Registry evidence:
> terrain `/api/v1/runnable-services`, live-verified 2026-09-22. The registry
> (terrain-mcp / terrain :8084) is the authoritative "what runs where" surface —
> consult it before any port assignment.

### Frozen assignments (live, registry-confirmed)

| Port | Owner | Framework | Registry status |
|------|-------|-----------|-----------------|
| 8080 | vd-ci-jenkins | Jenkins (CI) | ONLINE — not free |
| 8081 | service-broker/broker-gateway (hosts export, file, search, shrapnel-data, social-media, tackle-registry as embedded modules) | Spring Boot | ONLINE |
| 8082 | nexus-control-edge (AdonisJS/TS; currently the gateway's login target) | Node/TS | ONLINE — TS-owned |
| 8084 | terrain (also registered as topology-server) | Spring Boot | ONLINE |
| 8085 | service-registry | Spring Boot | ONLINE |
| 8090 | atlas | Spring Boot | ONLINE |
| 8091 | quarkus-broker-gateway | Quarkus | ONLINE (dev mode; own config still says 8090) |
| 8092 | — (freed 2026-09-22) | — | nexus-core JVM **retired on titanium** per the single-distro doctrine; now runs on **helium** `192.168.1.229:8092` (container `nexus-core/nexus-core:latest`, db vanadium, terrain id 140, probe-watched) |
| 8098 | peb-kernel (`python3 -m peb_kernel.main`) | Python | ONLINE — **Python owns 8098** |
| 9093 | helidon/user-access-service | Helidon MP | ONLINE |
| 9095-9097 | ballerina-ci-gateway / sonar-sync / jenkins-sync | Java (tooling) | ONLINE |

Free in the Spring/Quarkus band on titanium: 8083, 8086-8089, 8092, 8093-8097, 8099.

### Proposed assignments (decision items D1-D3)

| Port | Proposed owner | Rationale |
|------|----------------|-----------|
| 8094 | spring/service-broker/search-service (standalone, after fat-jar rollout) | gives the JVM search service its own port; today it runs embedded in broker-gateway :8081 and the gateway's `search-service` default points at terrain :8084 with no registry backing |
| 9093 (keep) | helidon/user-access-service = the JVM login surface | registered and online; the gateway's `login-service` default currently points at TS-owned 8082 instead |
| 8099 | spring/peb-kernel/peb-bootstrap (JVM peb; dormant until deployed) | 8098 is the live Python peb-kernel per the registry; the JVM bootstrap's `application.yml` claim on 8098 is a defect and must move |

### Rules

1. New JVM services take the lowest free port in their framework's band; never
   target a port owned by another service or language family.
2. The Quarkus gateway's `external.services.urls` defaults must point only at
   ports in this table. A default pointing at a foreign-owned or empty port is
   a defect: today that is `search-service → 8084` (terrain), `login-service →
   8082` (nexus-control-edge), and `user-service → 8083` (nothing registered,
   nothing listening).
3. Cross-language ownership is real: the Python peb-kernel owns 8098 until the
   roundtable rules otherwise; the JVM peb-bootstrap stays off it.
4. Any port change ratified here must be mirrored in the owning service's
   config and its launch units in the same change; the registry entry follows
   at the next registration heartbeat.
