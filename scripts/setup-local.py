"""Create a first-run local .env without overwriting existing configuration."""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(1, "Configuration already exists; it was not modified. Edit it manually.\n")

    password = getpass.getpass("Local administrator password (at least 8 characters): ")
    if len(password) < 8:
        parser.exit(1, "Password must contain at least 8 characters. No file was written.\n")
    if password != getpass.getpass("Confirm password: "):
        parser.exit(1, "Passwords do not match. No file was written.\n")

    from app.security import password_hash

    replacements = {
        "APP_ENV": "dev",
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "ADMIN_PASSWORD_HASH": password_hash(password),
        "LLM_PROVIDER": "disabled",
        "EMBEDDING_PROVIDER": "disabled",
        "LOW_RISK_ASSISTANCE": "false",
    }
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    content = "\n".join(
        f"{line.split('=', 1)[0]}={replacements[line.split('=', 1)[0]]}"
        if "=" in line and line.split("=", 1)[0] in replacements else line
        for line in lines
    ) + "\n"
    # Exclusive creation also protects against a concurrent setup overwriting secrets.
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(content)
    print("Local configuration created. Admin username: admin. Password is not stored in plaintext.")
    print("LLM and vectors are disabled initially. Follow docs/local-development.md to enable them.")


if __name__ == "__main__":
    main()
