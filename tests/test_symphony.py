"""Exercise the deploy opt-in boundary before Docker or credentials are touched."""
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from services.orchestrator.render_workflow import render

ROOT = Path(__file__).resolve().parents[1]

class SymphonyDeployTests(unittest.TestCase):
    def test_paused_workflow_is_valid_yaml_with_no_active_states(self):
        body, metadata = render("launch-lms", "a" * 40, b"# Product workflow\n", paused=True)
        frontmatter = yaml.safe_load(body.split("---\n", 2)[1])
        self.assertEqual(frontmatter["tracker"]["active_states"], [])
        self.assertEqual(len(metadata["rendered_workflow_sha256"]), 64)

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
