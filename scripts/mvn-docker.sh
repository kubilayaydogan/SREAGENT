#!/usr/bin/env bash
# scripts/build.sh ile aynı; çıktıyı target/build.log'a yazar ve özet gösterir.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/build.log"
"$ROOT/scripts/build.sh" "$@" > "$LOG" 2>&1; rc=$?
grep -E "Tests run:|BUILD|ERROR\]|FAIL|Reactor Summary|SUCCESS \[|FAILURE \[" "$LOG" | grep -v "^\s*at " | cut -c1-400 | tail -60
exit $rc
