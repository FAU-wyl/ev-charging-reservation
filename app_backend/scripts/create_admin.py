"""Explicit local ADMIN provisioning; never contacts ECURIOUS hardware."""

from __future__ import annotations

import getpass
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_backend.config import Settings
from app_backend.database import Database, now_iso
from app_backend.schemas import RegisterRequest
from app_backend.security import hash_password


def main() -> None:
    email = input("Admin email: ").strip().lower()
    password = getpass.getpass("Admin password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    validated = RegisterRequest(email=email, password=password)
    database = Database(Settings.from_env())
    database.initialize()
    with database.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO users(id, email, password_hash, role, created_at)
            VALUES (?, ?, ?, 'ADMIN', ?)
            ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash,
                role = 'ADMIN'
            """,
            (
                f"USR-{uuid.uuid4().hex[:12].upper()}",
                validated.email,
                hash_password(validated.password),
                now_iso(),
            ),
        )
    print(f"ADMIN provisioned for {validated.email}")


if __name__ == "__main__":
    main()
