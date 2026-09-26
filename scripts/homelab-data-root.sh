#!/usr/bin/env bash
# Print the data-plane git root (same rules as homelab_paths.data_root()).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -c "import homelab_paths as hp; print(hp.data_root())"
