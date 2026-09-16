"""Exercise the deploy opt-in boundary before Docker or credentials are touched."""
import hashlib
import importlib.util
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class SymphonyDeployTests(unittest.TestCase):
    def test_production_refused_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            script = root / 'scripts/deploy-symphony.sh'
            script.write_text((ROOT / 'scripts/deploy-symphony.sh').read_text())
            (root / '.deployment-environment').write_text('production\n')
            result = subprocess.run(['bash', str(script)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Symphony is dev-only', result.stderr)

    def test_unknown_environment_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            script = root / 'scripts/deploy-symphony.sh'
            script.write_text((ROOT / 'scripts/deploy-symphony.sh').read_text())
            (root / '.deployment-environment').write_text('staging\n')
            result = subprocess.run(['bash', str(script)], capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Symphony is dev-only', result.stderr)


class SymphonyStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('symphony_state', ROOT / 'scripts/symphony-state.py')
        cls.state = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.state)

    def write_manifest(self, archive):
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = archive.with_suffix(archive.suffix + '.json')
        manifest.write_text(json.dumps({'archive_sha256': checksum}))
        return manifest

    def test_offline_backup_verification_checks_integrity_and_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            (source / 'workspaces/BOT-253').mkdir(parents=True)
            (source / 'workspaces/BOT-253/workpad.md').write_text('synthetic')
            archive = root / 'symphony-home.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                bundle.add(source / 'workspaces', arcname='workspaces')
            result = self.state.verify(archive, self.write_manifest(archive))
            self.assertEqual(result['status'], 'verified')

    def test_offline_backup_verification_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'unsafe.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                member = tarfile.TarInfo('../secret')
                member.size = 0
                bundle.addfile(member)
            with self.assertRaisesRegex(RuntimeError, 'unsafe path'):
                self.state.verify(archive, self.write_manifest(archive))

    def test_preflight_rejects_observed_legacy_host_conditions(self):
        result = self.state.preflight({
            'running': True, 'health': 'healthy', 'home_volume': 'symphony-home',
            'oom_killed': True, 'memory_limit_bytes': 2_411_724_800,
            'memory': {'peak_bytes': 2_411_724_800, 'events': {'oom': 3, 'oom_kill': 2}},
            'host_memory': {'total_bytes': 4 * 1024**3, 'swap_total_bytes': 0},
        })
        self.assertFalse(result['passed'])
        self.assertTrue(any('OOM' in value for value in result['blockers']))
        self.assertTrue(any('below 8 GiB' in value for value in result['blockers']))

    def test_preflight_accepts_sized_healthy_destination(self):
        result = self.state.preflight({
            'running': True, 'health': 'healthy', 'home_volume': 'symphony-home',
            'oom_killed': False, 'memory_limit_bytes': 4 * 1024**3,
            'memory': {'peak_bytes': 2 * 1024**3, 'events': {'oom': 0, 'oom_kill': 0}},
            'host_memory': {'total_bytes': 8 * 1024**3, 'swap_total_bytes': 1024**3},
        })
        self.assertTrue(result['passed'])
        self.assertEqual(result['blockers'], [])
