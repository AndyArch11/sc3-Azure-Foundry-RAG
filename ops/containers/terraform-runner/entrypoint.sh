#!/usr/bin/env bash
set -euo pipefail

if [[ "${TF_IN_AUTOMATION:-}" == "" ]]; then
  export TF_IN_AUTOMATION=1
fi

exec "$@"
