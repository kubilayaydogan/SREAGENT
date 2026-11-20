# ADR-001 Sürüm seçimleri (sabitlendi: 19-20 Kasım 2026)

| Bileşen | Sürüm | Kaynak / gerekçe |
|---|---|---|
| Java | 21 LTS (Temurin, `eclipse-temurin:21-jre` → 21.0.13) | Talimat; Dynatrace Java tablosunda "Eclipse Temurin 21 LTS" listeli |
| Spring Boot | **4.1.3** | Maven Central'da 4.2.0 yeni çıkmıştı; springdoc 3.2.0 Boot 4.1.2'ye karşı derlenmiş → olgun 4.1.x yaması seçildi |
| springdoc-openapi | 3.2.0 | Maven Central latest; Boot 4.1 uyumlu |
| Flyway | 12.4.0 (Boot BOM) + `flyway-database-postgresql` | Boot yönetir; PG17 migration yerelde çalıştı |
| PostgreSQL JDBC | 42.7.14 (Boot BOM) | |
| HikariCP | 7.0.2 (Boot BOM) | |
| Micrometer | 1.17.3 (Boot BOM), registry-prometheus + registry-dynatrace | |
| Jackson | 3.x (`tools.jackson`, Boot 4 varsayılanı) | |
| Testcontainers | 2.0.5 (`testcontainers-postgresql`) | |
| HdrHistogram | 2.2.2 | |
| PostgreSQL | 17 (PGDG, Rocky 9) / yerel test `postgres:17` | Kullanıcı seçimi; `reserved_connections` (PG16+) kullanılıyor |
| Rocky Linux | 9 | Kullanıcı seçimi; OneAgent tablosunda "Rocky Linux 8, 9, 10 x86-64" |
| Python (collector) | 3.13-slim, psycopg[binary] 3.3.9 | PyPI latest |
| Maven (build container) | `maven:3.9-eclipse-temurin-21` | Yerel JDK/Maven gerektirmez |

Reddedilenler: Boot 4.2.0 (çok yeni, springdoc uyumu doğrulanmadı); Boot 3.5.x (gerek kalmadı).
Uygulama sırasında yeniden doğrulanmalı: OneAgent'ın Spring Boot 4 / Tomcat 11 / HikariCP 7 için özel destek
iddiası **dokümanda bulunamadı**; genel "JDBC 4+" ve Java 21 desteği bulundu.
