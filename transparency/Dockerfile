# ---- build ----------------------------------------------------------------
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /build

# Dependencies resolve in their own layer so source edits do not re-download the world.
COPY pom.xml .
RUN mvn -B -q dependency:go-offline

COPY src ./src
RUN mvn -B -q clean package -DskipTests=false

# ---- runtime --------------------------------------------------------------
FROM eclipse-temurin:17-jre-alpine AS runtime
WORKDIR /app

RUN addgroup -S app && adduser -S -G app app
COPY --from=build /build/target/efinance-powerbi-*.jar app.jar
USER app

EXPOSE 8080
ENV JAVA_OPTS="-XX:MaxRAMPercentage=75 -XX:+ExitOnOutOfMemoryError"

HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/actuator/health/readiness || exit 1

ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar"]
