#!/usr/bin/env bash
# Çalışan servislerden OpenAPI belgelerini docs/openapi/ altına yazar.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; H="${BASE_HOST:-127.0.0.1}"
for s in reservation:8081 checkin:8082 flightops:8083 baggage:8084; do
  curl -fsS "http://$H:${s#*:}/v3/api-docs.yaml" -o "$ROOT/docs/openapi/${s%%:*}.yaml" && echo "docs/openapi/${s%%:*}.yaml"
done
curl -fsS -u "${CHAOS_USER:-chaos}:${CHAOS_PASSWORD:?CHAOS_PASSWORD}" "http://$H:8080/v3/api-docs.yaml" -o "$ROOT/docs/openapi/chaos-controller.yaml" && echo docs/openapi/chaos-controller.yaml
