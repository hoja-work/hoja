"""Snapshot isolation and credential-exclusion regression tests (no production access)."""
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from sqlalchemy import select
from hoja_server.db import CURRENT_SCHEMA_REVISION, make_engine, metadata, migrate, seed_tenant

SPEC = importlib.util.spec_from_file_location('dev_snapshot', Path(__file__).parents[1] / 'scripts/dev_snapshot.py')
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


class SnapshotTests(unittest.TestCase):
    def test_copy_keeps_ui_data_without_live_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            source = make_engine(Path(directory) / 'source.db')
            migrate(source)
            seed_tenant(source, org_name='Private Workspace', org_url_key='private',
                        team_key='TEST', team_name='Testing', user_name='Real Person',
                        user_email='real@example.com', token='source-token')
            data = {'revision': CURRENT_SCHEMA_REVISION}
            with source.connect() as conn:
                for table in snapshot.TABLES:
                    data[table] = [dict(row) for row in conn.execute(select(metadata.tables[table])).mappings()]
            target = Path(directory) / 'target.db'
            token = snapshot.materialize(data, target)
            self.assertTrue(token.startswith('hoja_dev_'))
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(target) as conn:
                self.assertEqual(conn.execute('select name from organization').fetchone()[0], 'Private Workspace')
                # Who follows what is UI data: the inbox and Follow buttons read it.
                for table in ('project_subscriber', 'initiative_subscriber', 'cycle_subscriber'):
                    self.assertIn(table, snapshot.TABLES)
                    self.assertIn(table, snapshot.query('x'))
                for table in ('browser_session', 'password_credential', 'identity_credential',
                              'connector_account', 'model_provider', 'webhook', 'runner',
                              'agent_lease', 'mail_outbox', 'github_link'):
                    self.assertEqual(conn.execute(f'select count(*) from {table}').fetchone()[0], 0, table)
                self.assertEqual(conn.execute('select count(*) from api_key').fetchone()[0], 1)
                self.assertEqual(conn.execute('select token_hash from api_key').fetchone()[0], snapshot.token_hash(token))
                self.assertFalse(any('example.com' in row[0] for row in conn.execute('select email from "user"')))
            from hoja_server.store import Store
            target_engine = make_engine(target)
            store = Store(target_engine)
            raw, identity = store.create_browser_session_for_token(token)
            self.assertIsNotNone(store.resolve_browser_session(raw))
            # Old snapshots lacked membership: repair must preserve data/key and
            # make the already-issued browser cookie valid, idempotently.
            with sqlite3.connect(target) as conn:
                conn.execute('DELETE FROM organization_membership')
            self.assertIsNone(store.resolve_browser_session(raw))
            snapshot.repair_dev_membership(target)
            snapshot.repair_dev_membership(target)
            self.assertIsNotNone(store.resolve_browser_session(raw))
            with self.assertRaises(ValueError):
                snapshot.materialize(data, target)
            target_engine.dispose()
            source.dispose()

    def test_export_is_read_only_allowlisted_and_scoped(self):
        sql = snapshot.query("workspace'quoted")
        self.assertIn('REPEATABLE READ READ ONLY', sql)
        self.assertIn("workspace''quoted", sql)
        self.assertIn('statement_timeout', sql)
        self.assertNotIn('SELECT *', sql)
        self.assertNotIn('api_key', sql)
        self.assertNotIn('browser_session', sql)
        self.assertIn('view_id IN', sql)
        self.assertTrue(sql.endswith('ROLLBACK;\n'))

    def test_flag_order_and_missing_workspace_fail_before_dependencies(self):
        script = Path(__file__).parents[1] / 'scripts/dev-local.sh'
        for flags in (['--tailscale', '--snapshot'], ['--snapshot', '--tailscale']):
            result = subprocess.run(['bash', str(script), *flags], env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('HOJA_DEV_SNAPSHOT_ORG', result.stderr)

    def test_refuse_newer_schema_and_unknown_workspace(self):
        with self.assertRaises(ValueError):
            snapshot.sanitize({'revision': CURRENT_SCHEMA_REVISION + 1})
        with self.assertRaises(ValueError):
            snapshot.sanitize({'revision': CURRENT_SCHEMA_REVISION, 'organization': []})


if __name__ == '__main__':
    unittest.main()
