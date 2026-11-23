"""Seed rows the api-keys journey cannot create through the UI (HOJA-734 e2e only).

An OAuth grant needs a browser approval flow and a browser session needs a real sign-in, so the
journey inserts them straight into a *disposable* stack database:

    python seed_access.py <sqlite path> <user email> <tag>

Prints JSON {"grant": id, "sessions": [id, id]}. Never point this at a real database.
"""
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone


def main() -> None:
    path, email, tag = sys.argv[1:4]
    db = sqlite3.connect(path)
    row = db.execute("select id, organization_id from user where email = ?", (email,)).fetchone()
    if not row:
        raise SystemExit(f"no user {email}")
    uid, org = row
    now = datetime.now(timezone.utc)
    iso = now.isoformat()
    grant = str(uuid.uuid4())
    db.execute(
        "insert into oauth_grant (id, organization_id, user_id, client_id, client_name, device_label,"
        " scopes, created_at, updated_at, last_used_at, last_used_ip) values (?,?,?,?,?,?,?,?,?,?,?)",
        (grant, org, uid, "hoja-cli", f"Hoja CLI {tag}", "laptop", json.dumps(["hoja:read", "hoja:write"]),
         iso, iso, iso, "127.0.0.1"),
    )
    sessions = []
    for i in range(2):
        sid = str(uuid.uuid4())
        db.execute(
            "insert into browser_session (id, token_hash, user_id, organization_id, created_at, expires_at,"
            " last_seen_at) values (?,?,?,?,?,?,?)",
            (sid, f"e2e-{tag}-{i}-{uuid.uuid4().hex}", uid, org, (now - timedelta(hours=i + 1)).isoformat(),
             (now + timedelta(days=7)).isoformat(), iso),
        )
        sessions.append(sid)
    db.commit()
    print(json.dumps({"grant": grant, "sessions": sessions}))


if __name__ == "__main__":
    main()
