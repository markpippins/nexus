# syntax=docker/dockerfile:1
# helidon user-access-service — Helidon MP service (jar + libs runtime).
#
# Build context MUST be the jvm/ repo root:
#   docker build -f helidon/user-access-service/Dockerfile -t nexus-vanadium/helidon-user-access .
#
# The service's pom depends on three reactor siblings that no public Maven
# repository carries: com.aibizarchitect.nexus:nexus-user-api (jvm/shared/
# user-api), broker-service-api (jvm/shared/adapters.spring), and
# broker-service-spi (jvm/spring/service-broker/broker-service-spi). They
# must be installed into the local Maven repo BEFORE the service build —
# exactly the PR #454 class of gap (nexus-core peb-api) that made this
# image fail CI on 2026-09-23. The helidon-mp parent resolves from Central
# (empty relativePath), so it needs no pre-seed.
#
# NOTE: Dockerfile.jlink was removed 2026-09-23: it activated a
# `-Pjlink-image` profile that does not exist in this pom (verified — no
# jlink/profile block anywhere), so target/user-access-service-jri was never
# produced and the image could not build. Reintroduce it together with the
# pom profile if a custom Java Runtime Image is actually wanted.
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /seed
# Spring parent POM (com.aibizarchitect.nexus:nexus) — broker-service-spi's parent.
COPY spring/pom.xml spring-parent.pom.xml
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f spring-parent.pom.xml -N install
# shared/ reactor — carries nexus-user-api and broker-service-api.
COPY shared/ shared/
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f shared/pom.xml install -DskipTests
# broker-service-spi — standalone module under the spring parent.
COPY spring/service-broker/broker-service-spi/ spring/service-broker/broker-service-spi/
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -f spring/service-broker/broker-service-spi/pom.xml install -DskipTests
# The service build. go-offline tolerates its known false-failures (|| true);
# the authoritative resolution happens in `package`.
WORKDIR /helidon
COPY helidon/user-access-service/pom.xml .
RUN --mount=type=cache,target=/root/.m2 mvn -B -q dependency:go-offline || true
COPY helidon/user-access-service/src src
RUN --mount=type=cache,target=/root/.m2 mvn -B package -DskipTests

FROM eclipse-temurin:21-jre
WORKDIR /helidon
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /helidon/target/user-access-service.jar ./
COPY --from=build /helidon/target/libs ./libs
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -sf http://127.0.0.1:8080/health || exit 1
CMD ["java", "-XX:MaxRAMPercentage=75.0", "-jar", "user-access-service.jar"]
