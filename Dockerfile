# syntax=docker/dockerfile:1.7
# Tek Dockerfile, çok hedef: --target reservation | checkin | flightops | baggage | chaos | migrator
# Derleme container içinde JDK 21 + Maven ile yapılır; çalışma imajı yalnız JRE 21.

FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
COPY lab-common/pom.xml lab-common/
COPY db-migrator/pom.xml db-migrator/
COPY test-support/pom.xml test-support/
COPY reservation-service/pom.xml reservation-service/
COPY checkin-service/pom.xml checkin-service/
COPY flightops-service/pom.xml flightops-service/
COPY baggage-service/pom.xml baggage-service/
COPY chaos-controller/pom.xml chaos-controller/
COPY db-vm/create-roles.sql db-vm/chaos-v2-superuser.sql db-vm/
RUN --mount=type=cache,target=/root/.m2 mvn -B -ntp -q dependency:go-offline -DexcludeReactor=true || true
COPY . .
RUN --mount=type=cache,target=/root/.m2 mvn -B -ntp -q -DskipTests package \
 && mkdir -p /out \
 && cp reservation-service/target/reservation-service.jar /out/ \
 && cp checkin-service/target/checkin-service.jar /out/ \
 && cp flightops-service/target/flightops-service.jar /out/ \
 && cp baggage-service/target/baggage-service.jar /out/ \
 && cp chaos-controller/target/chaos-controller.jar /out/ \
 && cp db-migrator/target/db-migrator-*-exec.jar /out/db-migrator.jar

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
