import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wait_until_deploy_ready', ROOT/'scripts'/'wait-until-deploy-ready.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HELD = {'ready': False, 'reasons': [{'kind': 'user_test', 'run': 'Badge run', 'holds_until': '2026-11-11T03:00:00Z'}]}
READY = {'ready': True, 'reasons': []}


class WaitUntilDeployReady(unittest.TestCase):
    def run_wait(self, answers, max_wait=900):
        now = [0]
        asked = iter(answers)
        result = module.wait(ask=lambda: next(asked), sleep=lambda s: now.__setitem__(0, now[0] + s),
                             clock=lambda: now[0], interval=300, max_wait=max_wait)
        return result, now[0]

    def test_a_ready_site_deploys_straight_away(self):
        self.assertEqual(self.run_wait([READY]), ('site is ready', 0))

    def test_holds_while_the_site_says_no_then_goes(self):
        self.assertEqual(self.run_wait([HELD, HELD, READY]), ('site is ready', 600))

    def test_the_backstop_names_what_held_it(self):
        result, waited = self.run_wait([HELD] * 10)
        self.assertIn('deploying anyway', result)
        self.assertIn('Badge run', result)
        self.assertEqual(waited, 900)

    def test_a_site_that_cannot_say_does_not_hold_the_deploy(self):
        self.assertEqual(self.run_wait([None]), ("couldn't ask the site; not waiting", 0))

    def test_only_unstable_holds_and_a_newer_candidate_cancels_the_wait(self):
        workflow = (ROOT/'.github/workflows/deploy.yaml').read_text()
        hold = workflow.split('  hold:\n', 1)[1].split('\n  deploy:\n', 1)[0]
        self.assertIn("github.event_name == 'repository_dispatch' || inputs.environment == 'unstable'", hold)
        self.assertIn('group: hold-unstable', hold)
        self.assertIn('cancel-in-progress: true', hold)
        self.assertIn('scripts/wait-until-deploy-ready.py', hold)
        deploy = workflow.split('\n  deploy:\n', 1)[1]
        self.assertIn("needs.hold.result == 'skipped'", deploy)  # production never waits
        self.assertIn('cancel-in-progress: false', deploy)


if __name__ == '__main__':
    unittest.main()
