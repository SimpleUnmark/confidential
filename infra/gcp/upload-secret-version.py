"""Operator-only credential upload; Terraform must create the container first.

Prompts hide input; the payload goes to gcloud on stdin, not command arguments,
environment variables, a local file, Terraform, or shell history.
"""

from __future__ import annotations

import argparse
import getpass
import subprocess
import sys

SECRET_IDS = {
    "deepinfra": "simpleunmark-deepinfra-api-key",
    "shared": "simpleunmark-confidential-shared-secret",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("secret", choices=SECRET_IDS)
    parser.add_argument("--project", default="simple-unmark-prod")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error(
            "Run in your own interactive terminal; refusing non-interactive secret input."
        )
    value = getpass.getpass("Paste secret value (hidden): ")
    confirmation = getpass.getpass("Paste it again (hidden): ")
    if value != confirmation:
        parser.error("Values do not match; nothing uploaded.")
    if not value or value != value.strip() or any(ord(c) < 32 for c in value):
        parser.error(
            "Use a nonempty single-line secret without surrounding whitespace."
        )
    if args.secret == "shared" and len(value.encode()) < 32:
        parser.error("The HMAC secret must contain at least 32 bytes.")
    subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "add",
            SECRET_IDS[args.secret],
            f"--project={args.project}",
            "--data-file=-",
            "--format=value(name)",
        ],
        input=value.encode(),
        check=True,
    )


if __name__ == "__main__":
    main()
