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
            result = subprocess.run(['bash', str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Symphony is dev-only', result.stderr)

    def test_unknown_environment_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            script = root / 'scripts/deploy-symphony.sh'
            script.write_text((ROOT / 'scripts/deploy-symphony.sh').read_text())
            (root / '.deployment-environment').write_text('staging\n')
            result = subprocess.run(['bash', str(script)], capture_output=True, text=True)
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
