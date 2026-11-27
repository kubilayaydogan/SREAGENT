#!/usr/bin/env bash
# Docker içinde JDK 21 + Maven ile derler ve testleri koşar (yerelde JDK/Maven gerekmez).
# Kullanım: scripts/build.sh [maven argümanları]   (varsayılan: -B -ntp verify)
# Testcontainers için Docker soketi container'a bağlanır.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT"
command -v cygpath >/dev/null 2>&1 && SRC="$(cygpath -w "$ROOT")"   # Git Bash (Windows)
ARGS=("$@"); [[ ${#ARGS[@]} -eq 0 ]] && ARGS=(-B -ntp verify)
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$SRC":/src -w /src/lab \
  -v airlab-m2:/root/.m2 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal \
  --add-host host.docker.internal:host-gateway \
  maven:3.9-eclipse-temurin-21 mvn "${ARGS[@]}"
