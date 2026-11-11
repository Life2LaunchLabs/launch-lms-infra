import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wait_for_user_tests', ROOT/'scripts'/'wait-for-user-tests.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WaitForUserTests(unittest.TestCase):
    def run_wait(self, answers, max_wait=900):
        now = [0]
        asked = iter(answers)
        result = module.wait(ask=lambda: next(asked), sleep=lambda s: now.__setitem__(0, now[0] + s),
                             clock=lambda: now[0], interval=300, max_wait=max_wait)
        return result, now[0]

    def test_no_test_goes_straight_ahead(self):
        self.assertEqual(self.run_wait([False]), ('no test active', 0))

    def test_holds_while_a_test_is_active_then_goes(self):
        self.assertEqual(self.run_wait([True, True, False]), ('no test active', 600))

    def test_never_waits_past_the_cap(self):
        result, waited = self.run_wait([True] * 10)
        self.assertIn('deploying anyway', result)
        self.assertEqual(waited, 900)

    def test_an_app_that_cannot_say_does_not_hold_the_deploy(self):
        self.assertEqual(self.run_wait([None]), ("couldn't ask the app; not waiting", 0))

    def test_only_unstable_deploys_hold(self):
        workflow = (ROOT/'.github/workflows/deploy.yaml').read_text()
        step = workflow.split('Hold while a user test is active on unstable', 1)[1].split('- name:', 1)[0]
        self.assertIn("github.event_name == 'repository_dispatch' || inputs.environment == 'unstable'", step)
        self.assertIn('scripts/wait-for-user-tests.py', step)


if __name__ == '__main__':
    unittest.main()
