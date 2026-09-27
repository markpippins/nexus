# syntax=docker/dockerfile:1
# service-registry — JVM service discovery/registration (Spring Boot, :8085).
# Packaged for the vanadium failover tier. Needs PostgreSQL (registry schema)
# + Redis at runtime; configure via standard Spring env overrides:
#   SPRING_DATASOURCE_URL / SPRING_DATASOURCE_USERNAME / SPRING_DATASOURCE_PASSWORD
#   SPRING_DATA_REDIS_HOST (Boot 3.x)
# NOTE: no Maven wrapper in this repo; parent POM com.aibizarchitect.nexus:
# nexus must be installed first — hence build context = jvm/ repo root and
# dockerfile path spring/service-registry/Dockerfile.
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
# Parent POM com.aibizarchitect.nexus:nexus lives at jvm/spring/pom.xml
# (restored 2026-08-22 from the installed artifact — its source was dropped
# in a restructure). COPY it to its natural path AND install it
# non-recursively: service-registry's pom.xml resolves its parent via
# <relativePath>../pom.xml</relativePath> (file present in the build
# context at /build/spring/pom.xml), so the build no longer depends on a
# warm Maven cache for parent resolution — cold-cache runners (CI) were
# failing with 'Non-resolvable parent POM' before this.
COPY spring/pom.xml spring/pom.xml
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f spring/pom.xml -N install
# Sibling API modules service-registry compiles against (normally provided
# by host ~/.m2): install them first so the container build is self-contained.
COPY shared/broker-service-api/pom.xml shared/broker-service-api/
COPY shared/broker-service-api/src shared/broker-service-api/src
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f shared/broker-service-api/pom.xml install
COPY shared/service-registry-api/pom.xml shared/service-registry-api/
COPY shared/service-registry-api/src shared/service-registry-api/src
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f shared/service-registry-api/pom.xml install
COPY pom.xml .
COPY spring/service-registry/pom.xml spring/service-registry/
COPY spring/service-registry/src spring/service-registry/src
WORKDIR /build/spring/service-registry
RUN --mount=type=cache,target=/root/.m2 mvn -B clean package -DskipTests -q \
 && JAR=$(ls target/*.jar | grep -v original | head -1) \
 && ln -s "$(basename "$JAR")" target/app.jar

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/spring/service-registry/target/app.jar app.jar
EXPOSE 8085
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -sf http://127.0.0.1:8085/actuator/health || exit 1
CMD ["java", "-jar", "app.jar"]
