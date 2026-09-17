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
# jvm/spring/pom.xml installed first. It DOES consume the shared canonical
# modules (com.aibizarchitect.nexus:core + adapters.spring —
# BinaryData/ResponseError/PagedResponse), so jvm/shared/ is installed into
# the local repo before the reactor build, exactly like the broker-gateway
# image build.
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
# Standalone nexus-core reactor — parent POM resolves from Central.
COPY spring/nexus-core/ spring/nexus-core/
WORKDIR /build/spring/nexus-core
RUN --mount=type=cache,target=/root/.m2 mvn -B clean package -DskipTests -q \
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
