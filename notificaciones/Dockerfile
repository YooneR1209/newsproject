# Notificaciones: backend Spring Boot + PostgreSQL en el mismo contenedor (supervisord).

# 1) Compilar el .jar
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
RUN mvn -q -B dependency:go-offline
COPY src ./src
RUN mvn -q -B package -DskipTests

# 2) Imagen final: PostgreSQL + JRE 21 + supervisord
FROM postgres:16
COPY --from=eclipse-temurin:21-jre /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk PATH="/opt/java/openjdk/bin:${PATH}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends supervisor curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /src/target/notificaciones.jar /app/notificaciones.jar
COPY supervisord.conf /etc/supervisor/conf.d/app.conf

EXPOSE 8080
ENTRYPOINT []
CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
