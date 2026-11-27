# syntax=docker/dockerfile:1.7
# Tek Dockerfile, çok hedef: --target reservation | checkin | flightops | baggage | chaos | migrator
# Build context repo köküdür (rol betikleri db-vm/ altından gelir): docker build -f lab/Dockerfile .
# Derleme container içinde JDK 21 + Maven ile yapılır; çalışma imajı yalnız JRE 21.

FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src/lab
COPY lab/pom.xml .
COPY lab/lab-common/pom.xml lab-common/
COPY lab/db-migrator/pom.xml db-migrator/
COPY lab/test-support/pom.xml test-support/
COPY lab/reservation-service/pom.xml reservation-service/
COPY lab/checkin-service/pom.xml checkin-service/
COPY lab/flightops-service/pom.xml flightops-service/
COPY lab/baggage-service/pom.xml baggage-service/
COPY lab/chaos-controller/pom.xml chaos-controller/
COPY db-vm/create-roles.sql db-vm/chaos-v2-superuser.sql /src/db-vm/
RUN --mount=type=cache,target=/root/.m2 mvn -B -ntp -q dependency:go-offline -DexcludeReactor=true || true
COPY lab/ .
RUN --mount=type=cache,target=/root/.m2 mvn -B -ntp -q -DskipTests package \
 && mkdir -p /out \
 && cp reservation-service/target/reservation-service.jar /out/ \
 && cp checkin-service/target/checkin-service.jar /out/ \
 && cp flightops-service/target/flightops-service.jar /out/ \
 && cp baggage-service/target/baggage-service.jar /out/ \
 && cp chaos-controller/target/chaos-controller.jar /out/ \
 && cp db-migrator/target/db-migrator-*-exec.jar /out/db-migrator.jar

# Chaos v2 React UI (yalnız build; çıktı chaos imajına kopyalanır)
FROM node:22-alpine AS chaos-ui
WORKDIR /ui
COPY lab/chaos-controller/ui/package.json lab/chaos-controller/ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY lab/chaos-controller/ui/ ./
RUN npx tsc -b --noEmit && npx vitest run && npx vite build

FROM eclipse-temurin:21-jre AS runtime-base
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
# Konteyner bellek limitine göre heap; GC ve heap bilgisi JVM metriklerinde görünür
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=70 -XX:+UseG1GC -XX:+ExitOnOutOfMemoryError -Duser.timezone=UTC -Dfile.encoding=UTF-8"
USER app

FROM runtime-base AS reservation
COPY --from=build /out/reservation-service.jar app.jar
EXPOSE 8081
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s CMD curl -fs http://localhost:8081/actuator/health/liveness || exit 1
ENTRYPOINT ["java", "-jar", "/app/app.jar"]

FROM runtime-base AS checkin
COPY --from=build /out/checkin-service.jar app.jar
EXPOSE 8082
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s CMD curl -fs http://localhost:8082/actuator/health/liveness || exit 1
ENTRYPOINT ["java", "-jar", "/app/app.jar"]

FROM runtime-base AS flightops
COPY --from=build /out/flightops-service.jar app.jar
EXPOSE 8083
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s CMD curl -fs http://localhost:8083/actuator/health/liveness || exit 1
ENTRYPOINT ["java", "-jar", "/app/app.jar"]

FROM runtime-base AS baggage
COPY --from=build /out/baggage-service.jar app.jar
EXPOSE 8084
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s CMD curl -fs http://localhost:8084/actuator/health/liveness || exit 1
ENTRYPOINT ["java", "-jar", "/app/app.jar"]

FROM runtime-base AS chaos
COPY --from=build /out/chaos-controller.jar app.jar
COPY --from=chaos-ui /ui/dist /app/static-ui/ui
USER root
RUN mkdir -p /data/experiments && chown -R app:app /data
USER app
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s CMD curl -fs http://localhost:8080/actuator/health || exit 1
ENTRYPOINT ["java", "-jar", "/app/app.jar"]

FROM runtime-base AS migrator
COPY --from=build /out/db-migrator.jar app.jar
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
