"""Management commands.

python -m app.cli create-admin admin@example.com --role owner   # prompts for password
"""

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import make_engine, make_sessionmaker
from app.models import AdminRole, AdminUser


async def create_admin(email: str, password: str, role: AdminRole) -> None:
    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            email = email.strip().lower()
            user = await db.scalar(select(AdminUser).where(AdminUser.email == email))
            if user is None:
                db.add(AdminUser(email=email, password_hash=hash_password(password), role=role))
                action = "created"
            else:
                user.password_hash, user.role, user.disabled = hash_password(password), role, False
                action = "updated"
            await db.commit()
            print(f"{action} {role.value} {email}")
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin", help="create or reset an admin user")
    p.add_argument("email")
    p.add_argument("--role", choices=[r.value for r in AdminRole], default="owner")
    args = parser.parse_args(argv)

    password = os.environ.get("LIVEOPS_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    if len(password) < 10:
        sys.exit("password must be at least 10 characters")
    asyncio.run(create_admin(args.email, password, AdminRole(args.role)))


if __name__ == "__main__":
    main()
