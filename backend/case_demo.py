"""Provision one fresh synthetic demo from a caller-owned password mapping."""
from __future__ import annotations

import json
import re
import sys

import httpx
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.core.config import Profile, settings
from app.db.session import SessionLocal
from app.models.user import User
from app.seed.demo import run_demo_seed
from app.seed.users import DEMO_ACCOUNTS, seed_demo_users


def main() -> None:
    url = make_url(settings.database_url)
    if (settings.profile is not Profile.DEMO or url.host != "postgres" or url.port != 5432
            or not re.fullmatch(r"jiuye_case_demo_[a-f0-9]{16}", url.database or "")
            or not re.fullmatch(r"jiuye-case-demo-[a-f0-9]{32}", settings.installation_id)):
        raise RuntimeError("refusing non-case demo target")
    passwords = json.load(sys.stdin)
    if (set(passwords) != set(DEMO_ACCOUNTS)
            or any(not isinstance(v, str) or len(v) < 40 for v in passwords.values())):
        raise RuntimeError("invalid generated account mapping")
    with SessionLocal() as db:
        if db.execute(text("SELECT current_database()")).scalar_one() != url.database:
            raise RuntimeError("database identity mismatch")
        counts = run_demo_seed(db, reset=False, commit=False)
        counselor = db.execute(select(User).where(User.username == "counselor")).scalar_one()
        seed_demo_users(db, passwords=passwords, counselor_college_id=counselor.college_id)
    roles = []
    with httpx.Client(base_url="http://localhost:8000", timeout=20, trust_env=False) as client:
        for username, password in passwords.items():
            response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
            if response.status_code != 200:
                raise RuntimeError("HTTP login smoke failed")
            token = response.json()["access_token"]
            me = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + token})
            if me.status_code != 200 or me.json()["username"] != username:
                raise RuntimeError("HTTP identity smoke failed")
            roles.append(username)
    print(json.dumps({"synthetic_counts": counts, "http_login_accounts": roles}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # HTTP / SQL tracebacks may contain ephemeral credentials. Report the type only.
        print(f"[demo] FAILED: {type(error).__name__}; preserve this instance for investigation.", file=sys.stderr)
        raise SystemExit(1)
