# syntax=docker/dockerfile:1
# nexus-core — read-only JVM projection monolith (Spring Boot, :8092).
# Assembles nexus-core-search + -broker + -solscript + -writequeue into ONE
# boot-executable jar via nexus-core-app. Read posture: PG connection is
# SELECT-only by convention (writes 405 / queue via NATS JetStream), so the
# container is safe to point at a replica or backup host (vanadium).
#
# Build context MUST be the jvm/ repo root:
#   docker build -f spring/nexus-core/Dockerfile -t nexus-vanadium/nexus-core .
# nexus-core is a STANDALONE multi-module project (parent =
# spring-boot-starter-parent from Central), so it does NOT need
# jvm/spring/pom.xml installed first. It DOES consume two external Maven
# reactors that must be installed into the local repo before its build:
#   1. jvm/shared/ (com.aibizarchitect.nexus:core + adapters.spring —
#      BinaryData/ResponseError/PagedResponse)
#   2. jvm/spring/peb-kernel/ (org.nexus.peb:peb-api — imported by
#      NexusCoreApplication; peb-kernel is its own standalone reactor,
#      parent from Central, no dependency on shared/)
# exactly like the broker-gateway image build.
#
# Runtime configuration (standard Spring env overrides, see
# nexus-core-app/src/main/resources/application.properties):
#   NEXUS_CORE_DB_URL      jdbc:postgresql://<host>:5432/nexus
#   NEXUS_CORE_DB_USER     pguser
#   NEXUS_CORE_DB_PASSWORD pgpass
#   NATS_URL               nats://<host>:4222 (optional; lazy + graceful)
#   SERVER_PORT            8092 (default)
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
# Shared canonical modules (com.aibizarchitect.nexus) must be in the local
# repo before nexus-core resolves.
COPY shared/ shared/
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f shared/pom.xml install -DskipTests
# peb-api (org.nexus.peb) — separate standalone reactor, must be in the
# local repo before nexus-core resolves its app-module dependency.
COPY spring/peb-kernel/ spring/peb-kernel/
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f spring/peb-kernel/pom.xml install -DskipTests
# Standalone nexus-core reactor — parent POM resolves from Central.
COPY spring/nexus-core/ spring/nexus-core/
WORKDIR /build/spring/nexus-core
# Re-install shared + peb-kernel INSIDE this step before packaging.
#
# Why: the earlier install RUNs above can be CACHED-skipped by BuildKit,
# and a skipped RUN performs NO cache-mount writes. GitHub runners are
# ephemeral, so the /root/.m2 cache mount is cold on every run — a PR that
# invalidates the COPY layers (any file under spring/nexus-core/**, e.g.
# regenerated API.md) then re-executes ONLY this step and dies with
# "Could not find artifact com.aibizarchitect.nexus:core:jar:0.1.0-SNAPSHOT"
# (exposed by PR #500; main's green runs were full cache hits and never
# exercised the path). These installs are idempotent — they succeed and
# install the artifacts whether the mount is cold or already warm.
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f /build/shared/pom.xml install -DskipTests \
 && mvn -B -q -f /build/spring/peb-kernel/pom.xml install -DskipTests \
 && mvn -B clean package -DskipTests -q \
 && JAR=$(ls nexus-core-app/target/*.jar | grep -v original | head -1) \
 && ln -s "$(basename "$JAR")" nexus-core-app/target/app.jar

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/spring/nexus-core/nexus-core-app/target/app.jar app.jar
EXPOSE 8092
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -sf http://127.0.0.1:8092/actuator/health || exit 1
CMD ["java", "-XX:MaxRAMPercentage=75.0", "-jar", "app.jar"]
