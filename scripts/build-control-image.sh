#!/usr/bin/env bash
# Build homelab/control image from repo Dockerfile.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:-homelab/control:local}"
docker build -t "${TAG}" "${ROOT}"
echo "Built ${TAG}"
