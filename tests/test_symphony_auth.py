import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ApiAuthenticationTests(unittest.TestCase):
    def run_entrypoint(self, key=None, login_fails=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            secret = root / 'key'
            if key is not None:
                secret.write_text(key)
            marker = root / 'login'
            codex = bin_dir / 'codex'
            codex.write_text('#!/bin/sh\ncat >/dev/null\nprintf api >"$LOGIN_MARKER"\nexit '+('1' if login_fails else '0')+'\n')
            codex.chmod(0o755)
            # End after authentication; no Git/network/Symphony side effects.
            script = root / 'entrypoint.sh'
            script.write_text((ROOT / 'symphony/entrypoint.sh').read_text().split(': "${GH_TOKEN')[0]+'echo AUTH_READY\n')
            env = dict(os.environ, HOME=str(root / 'home'), PATH=str(bin_dir)+':'+os.environ['PATH'], SYMPHONY_API_KEY_FILE=str(secret), LOGIN_MARKER=str(marker))
            result = subprocess.run(['bash', str(script)], env=env, text=True, capture_output=True)
            return result, marker.exists()

    def test_missing_key_never_uses_chatgpt(self):
        result, called = self.run_entrypoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(called)
        self.assertIn('Missing worker API key', result.stderr)

    def test_key_is_stdin_only_and_not_logged(self):
        result, called = self.run_entrypoint('fake-test-secret')
        self.assertEqual(result.returncode, 0)
        self.assertTrue(called)
        self.assertNotIn('fake-test-secret', result.stdout + result.stderr)
        self.assertIn('AUTH_READY', result.stdout)

    def test_failed_login_stops_dispatch(self):
        result, called = self.run_entrypoint('fake-test-secret', login_fails=True)
        self.assertTrue(called)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('AUTH_READY', result.stdout)
