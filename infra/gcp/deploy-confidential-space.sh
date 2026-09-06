#!/usr/bin/env bash
set -Eeuo pipefail

# Disabled: this legacy path injected plaintext credentials into VM metadata.
echo "Use infra/gcp/terraform and secret-manager-migration.md; this legacy script is disabled." >&2
exit 2
