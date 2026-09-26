#!/usr/bin/env bash
# Re-decrypt all config/secrets/* into /run/homelab (after reboot / before deploy).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_ROOT="$("${SCRIPT_DIR}/homelab-data-root.sh")"
export HOMELAB_DATA_ROOT="${DATA_ROOT}"
cd "${DATA_ROOT}"
PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" python3 - <<'PY'
import sys

from sops_secrets import decrypt_to, ensure_runtime, SECRETS_DIR  # noqa: E402

ensure_runtime()
if not SECRETS_DIR.is_dir():
    print(f"No {SECRETS_DIR}; nothing to decrypt", file=sys.stderr)
    raise SystemExit(0)

ok = 0
for path in sorted(SECRETS_DIR.iterdir()):
    if not path.is_file() or path.name.startswith("."):
        continue
    try:
        out = decrypt_to(path.name)
        print(f"ok {path.name} -> {out}")
        ok += 1
    except Exception as exc:
        print(f"FAIL {path.name}: {exc}", file=sys.stderr)
        raise SystemExit(1)
print(f"Decrypted {ok} secret file(s) into runtime dir")
PY
