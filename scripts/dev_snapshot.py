"""Read one production workspace into a credential-free, disposable SQLite copy.

Only explicitly allowlisted UI data is exported. Descriptions/comments remain
private production content: this is not an anonymized or shareable fixture.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import tempfile

from hoja_server.db import (CURRENT_SCHEMA_REVISION, make_engine, metadata, migrate,
                            new_id, now_iso, token_hash)

# No credentials, sessions, integrations, attachments, runners, jobs, agent
# conversations, audit payloads, notification delivery or provisioning state.
TABLES = (
    "organization", "user", "team", "workflow_state", "team_member",
    "issue", "issue_identifier_alias", "issue_label", "issue_label_link",
    "issue_subscriber", "comment", "project", "project_status", "project_member",
    "project_team", "cycle", "initiative", "initiative_project", "project_update",
    "initiative_update", "saved_view", "saved_view_favorite",
)


def query(org):
    # Slug is a SQL literal, never an identifier. All identifiers are constants.
    literal = "'" + org.replace("'", "''") + "'"
    predicates = {
        "organization": "id IN (SELECT id FROM chosen)",
        "team_member": 'team_id IN (SELECT id FROM team WHERE organization_id IN (SELECT id FROM chosen))',
        "issue_label_link": 'issue_id IN (SELECT id FROM issue WHERE organization_id IN (SELECT id FROM chosen))',
        "issue_subscriber": 'issue_id IN (SELECT id FROM issue WHERE organization_id IN (SELECT id FROM chosen))',
        "project_member": 'project_id IN (SELECT id FROM project WHERE organization_id IN (SELECT id FROM chosen))',
        "project_team": 'project_id IN (SELECT id FROM project WHERE organization_id IN (SELECT id FROM chosen))',
        "initiative_project": 'initiative_id IN (SELECT id FROM initiative WHERE organization_id IN (SELECT id FROM chosen))',
        "saved_view_favorite": 'view_id IN (SELECT id FROM saved_view WHERE organization_id IN (SELECT id FROM chosen))',
    }
    pairs = []
    for table in TABLES:
        predicate = predicates.get(table, 'organization_id IN (SELECT id FROM chosen)')
        columns = ','.join(f'"{column.name}"' for column in metadata.tables[table].columns)
        pairs.append(f"'{table}', (SELECT COALESCE(json_agg(t), '[]'::json) FROM (SELECT {columns} FROM \"{table}\" WHERE {predicate}) t)")
    return ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
            "SET LOCAL statement_timeout = '30s';\n"
            f"WITH chosen AS (SELECT id FROM organization WHERE url_key = {literal}) "
            "SELECT json_build_object('revision', (SELECT max(revision) FROM schema_revision), "
            + ", ".join(pairs) + ");\nROLLBACK;\n")


def sanitize(data):
    if data["revision"] != CURRENT_SCHEMA_REVISION:
        raise ValueError("Snapshot schema differs from this checkout; update dev before copying.")
    if len(data["organization"]) != 1:
        raise ValueError("No unique workspace matched HOJA_DEV_SNAPSHOT_ORG.")
    org = data["organization"][0]
    org["vanity_host"] = None
    for table in TABLES:
        for row in data[table]:
            # Prevent links and remote avatar fetches from pointing back to prod.
            for key in ("url", "avatar_url"):
                if key in row:
                    row[key] = None
    for team in data["team"]:
        team["cadence_enabled"] = False
    for issue in data["issue"]:
        issue["delegate_id"] = None
    for index, user in enumerate(data["user"]):
        user["email"] = f"person-{index}@snapshot.invalid"
    return data


def materialize(data, destination):
    data = sanitize(data)
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Snapshot destination already exists; use HOJA_DEV_RESET=1 to refresh.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # No raw export on disk. Publish the sanitized database only after success.
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        os.chmod(directory, 0o700)
        temp = Path(directory) / "snapshot.db"
        engine = make_engine(temp)
        migrate(engine)
        engine.dispose()
        token = "hoja_dev_" + secrets.token_hex(24)
        with sqlite3.connect(temp) as conn:
            for table in TABLES:
                for row in data[table]:
                    columns = list(row)
                    values = [json.dumps(row[c]) if isinstance(row[c], (list, dict)) else row[c] for c in columns]
                    conn.execute(f'INSERT INTO "{table}" (' + ','.join(f'"{c}"' for c in columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')', values)
            uid, org_id = new_id(), data["organization"][0]["id"]
            conn.execute('INSERT INTO "user" (id,organization_id,name,email,active,admin,kind) VALUES (?,?,?,?,1,1,?)',
                         (uid, org_id, "Snapshot Developer", "dev@snapshot.invalid", "human"))
            identity_id = new_id()
            conn.execute('INSERT INTO global_identity (id,email,created_at) VALUES (?,?,?)',
                         (identity_id, 'dev@snapshot.invalid', now_iso()))
            conn.execute('INSERT INTO organization_membership (id,identity_id,organization_id,profile_user_id,active,created_at) VALUES (?,?,?,?,1,?)',
                         (new_id(), identity_id, org_id, uid, now_iso()))
            conn.execute('INSERT INTO api_key (id,token_hash,label,access,user_id,organization_id,created_at) VALUES (?,?,?,?,?,?,?)',
                         (new_id(), token_hash(token), "Snapshot dev only", "read_write", uid, org_id, now_iso()))
            for team in data["team"]:
                conn.execute('INSERT INTO team_member (team_id,user_id,role) VALUES (?,?,?)', (team["id"], uid, "owner"))
            if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('Snapshot contains references outside the selected workspace.')
            conn.commit()
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        os.chmod(temp, 0o600)
        temp.replace(destination)
    return token


def repair_dev_membership(destination):
    """Upgrade older local snapshots; never reads or writes production."""
    with sqlite3.connect(destination) as conn:
        row = conn.execute('SELECT u.id,u.organization_id FROM "user" u JOIN api_key k ON k.user_id=u.id AND k.organization_id=u.organization_id WHERE u.email=? AND k.label=?',
                           ('dev@snapshot.invalid', 'Snapshot dev only')).fetchone()
        if row is None:
            raise ValueError('Snapshot dev principal is missing; refresh the snapshot.')
        uid, org_id = row
        if conn.execute('SELECT 1 FROM organization_membership WHERE profile_user_id=? AND organization_id=? AND active=1', (uid, org_id)).fetchone():
            return
        identity_id = new_id()
        conn.execute('INSERT INTO global_identity (id,email,created_at) VALUES (?,?,?)',
                     (identity_id, 'dev@snapshot.invalid', now_iso()))
        conn.execute('INSERT INTO organization_membership (id,identity_id,organization_id,profile_user_id,active,created_at) VALUES (?,?,?,?,1,?)',
                     (new_id(), identity_id, org_id, uid, now_iso()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--token-file')
    parser.add_argument('--repair-membership', action='store_true')
    args = parser.parse_args()
    if args.repair_membership:
        repair_dev_membership(args.db)
        return
    if not args.token_file:
        parser.error('--token-file is required when creating a snapshot')
    org = os.environ.get('HOJA_DEV_SNAPSHOT_ORG', '')
    if not org:
        parser.error('Set HOJA_DEV_SNAPSHOT_ORG to the production workspace URL key (for example hoja).')
    docker = shutil.which('docker')
    if not docker:
        # The Nix binary may require sudo just to traverse its profile directory.
        docker = '/opt/hoja/nix/result/bin/docker'
    command = ['sudo', '-n', docker, 'exec', '-i', 'hoja-db', 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'cliniar', '-d', 'cliniar']
    try:
        result = subprocess.run(command, input=query(org), text=True, capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit('Production export unavailable or timed out. No export was saved.') from None
    if result.returncode:
        raise SystemExit('Read-only production export failed; check local Docker/sudo access and workspace key. No export was saved.')
    try:
        token = materialize(json.loads(result.stdout), args.db)
    except Exception:
        # SQL errors can include row contents. Never echo private data in logs.
        raise SystemExit('Snapshot import failed; no database was published. Check schema compatibility and workspace data.') from None
    fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(token + '\n')
    print('Created private local snapshot. Production credentials and integrations were not copied.')


if __name__ == '__main__':
    main()
