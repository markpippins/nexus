# syntax=docker/dockerfile:1
# broker-gateway — the service-broker MONOLITH FACADE (W1 ruling, 2026-08-23).
# BrokerGatewayApplication aggregates file/login/user-access/user-creation/
# search/admin-logging/discovery in-process; this image is ONE deployable for
# the vanadium failover tier. No per-module split (module images only IF a
# module ever needs independent scaling).
#
# Build context MUST be the jvm/ repo root:
#   docker build -f spring/service-broker/broker-gateway/Dockerfile -t nexus-vanadium/broker-gateway .
# (No Maven wrapper in this repo; parent POM com.aibizarchitect.nexus:nexus
# must be installed first — same pattern as spring/service-registry.)
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
# Parent POM com.aibizarchitect.nexus:nexus lives at jvm/spring/pom.xml.
COPY spring/pom.xml spring-parent.pom.xml
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f spring-parent.pom.xml -N install
# Full shared/ reactor: gateway's dependency chain reaches several of its
# modules transitively (core, apis, adapters) via the service-broker siblings.
COPY shared/ shared/
COPY spring/atlas/ spring/atlas/
COPY spring/service-broker/ spring/service-broker/
# Broker reactor: build gateway plus its internal dependencies (-am), exactly
# like the documented host build. The pom repackages WITHOUT the exec
# classifier ("the main artifact IS the boot jar", atlas-consistent), so the
# bootable jar is the unclassified one — exclude the original-*.jar thin copy.
# The service-broker aggregator also declares module ../atlas (i.e. spring/atlas).
#
# The shared/ seed install and the package live in ONE RUN layer: cache mounts
# (--mount=type=cache) are not captured by the build layer cache, so a
# cache-hit seed layer leaves /root/.m2 EMPTY on a fresh CI runner and the
# -am build cannot resolve com.aibizarchitect.nexus:broker-service-api
# (jvm/shared — not a service-broker module). That cold-build failure is
# what PR #513 exposed 2026-09-24 (#454 drift class); same-RUN install
# guarantees sibling resolution on every cold build.
WORKDIR /build
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f shared/pom.xml install -DskipTests \
 && cd spring/service-broker \
 && mvn -B clean package -DskipTests -pl broker-gateway -am \
 && JAR=$(ls broker-gateway/target/*.jar | grep -v original | head -1) \
 && ln -s "$(basename "$JAR")" broker-gateway/target/app.jar

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/spring/service-broker/broker-gateway/target/app.jar app.jar
EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -sf http://127.0.0.1:8081/actuator/health || exit 1
CMD ["java", "-jar", "app.jar"]
