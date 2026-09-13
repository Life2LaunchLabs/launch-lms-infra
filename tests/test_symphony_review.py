import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('review_gate', ROOT / 'symphony/review_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
SHA = 'a' * 40

class ReviewGateTests(unittest.TestCase):
    def good_pr(self):
        return {'state': 'OPEN', 'isDraft': False, 'baseRefName': 'dev', 'headRefOid': SHA,
                'statusCheckRollup': [{'name': x, 'conclusion': 'SUCCESS'} for x in gate.REQUIRED]}

    def test_exact_reviewed_head_and_all_checks_required(self):
        pr = self.good_pr()
        gate.check_pr(pr, SHA)
        with self.assertRaises(ValueError):
            gate.check_pr(pr, 'b' * 40)
        pr['statusCheckRollup'].pop()
        with self.assertRaises(ValueError):
            gate.check_pr(pr, SHA)

    def test_pending_failed_browser_and_wrong_base_rejected(self):
        for conclusion in ('FAILURE', None, 'SKIPPED'):
            pr = self.good_pr()
            pr['statusCheckRollup'].append({'name': 'browser-ui / smoke', 'conclusion': conclusion})
            with self.assertRaises(ValueError):
                gate.check_pr(pr, SHA)
        pr = self.good_pr()
        pr['baseRefName'] = 'main'
        with self.assertRaises(ValueError):
            gate.check_pr(pr, SHA)

    def test_path_traversal_and_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'BOT-217'
            workspace.mkdir()
            (root / 'secret.txt').write_text('private')
            (workspace / 'escape.txt').symlink_to(root / 'secret.txt')
            for name in ('../secret.txt', 'escape.txt'):
                with self.assertRaises(ValueError):
                    gate.evidence_paths(workspace, [name])
            (workspace / 'report.md').write_text('synthetic evidence')
            self.assertEqual(len(gate.evidence_paths(workspace, ['report.md'])), 1)

    def test_upload_failure_never_transitions_to_review(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'BOT-217'
            root.mkdir()
            (root / 'report.md').write_text('report')
            manifest = root / '.symphony-review-request.json'
            manifest.write_text(json.dumps({'issue': 'BOT-217', 'sha': SHA, 'pr': 72,
                'synthetic_evidence_only': True, 'files': ['report.md'], 'summary': 'review'}))
            state = {'fields': {'issuetype': {'subtask': False}, 'status': {'name': 'In Progress'}, 'labels': ['symphony']}}
            with patch.object(gate, 'jira', return_value=state), patch.object(gate, 'pr_info', return_value=self.good_pr()), patch.object(gate, 'upload', side_effect=RuntimeError('upload failed')), patch.object(gate, 'transition') as transition:
                with self.assertRaises(RuntimeError):
                    gate.submit(manifest)
                transition.assert_not_called()

    def test_workflow_uses_status_gate_and_pause_covers_merge(self):
        workflow = (ROOT / 'symphony/WORKFLOW.md').read_text()
        self.assertIn('active_states: ["To Do", "In Progress", "Merge"]', workflow)
        self.assertIn('Never move an issue into Merge', workflow)
        self.assertIn('--match-head-commit <reviewed-sha>', workflow)
        self.assertIn("s/^  active_states:.*/  active_states: []/", (ROOT / 'symphony/entrypoint.sh').read_text())
