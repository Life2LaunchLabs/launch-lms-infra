import importlib.util
import subprocess
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wait_until_deploy_ready', ROOT/'scripts'/'wait-until-deploy-ready.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HELD = {'ready': False, 'reasons': [{'kind': 'user_test', 'run': 'Badge run', 'holds_until': '2026-11-11T03:00:00Z'}]}
READY = {'ready': True, 'reasons': []}
ANSWER_READY = ('answer', READY)


class WaitUntilDeployReady(unittest.TestCase):
    def run_wait(self, answers, max_wait=900):
        now = [0]
        asked = iter(answers)

        def ask():
            answer = next(asked)
            if isinstance(answer, Exception):
                raise answer
            return answer if isinstance(answer, tuple) else ('answer', answer)
        result = module.wait(ask=ask, sleep=lambda s: now.__setitem__(0, now[0] + s),
                             clock=lambda: now[0], interval=300, max_wait=max_wait, retries=3, pause=30)
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

    def test_a_site_that_is_down_is_retried_then_deploys(self):
        down = ('down', 'the site answered 502')
        self.assertEqual(self.run_wait([down, down, down]), ('the site answered 502; not waiting', 60))
        self.assertEqual(self.run_wait([down, ANSWER_READY]), ('site is ready', 30))

    def test_an_image_without_the_check_deploys(self):
        self.assertEqual(self.run_wait([('go', 'this image has no deploy-ready check')]),
                         ('this image has no deploy-ready check; not waiting', 0))

    def test_a_host_that_cannot_look_fails_instead_of_deploying(self):
        with self.assertRaises(module.CantLook):
            self.run_wait([module.CantLook('docker exec failed')] * 3)
        self.assertEqual(self.run_wait([module.CantLook('blip'), ANSWER_READY]), ('site is ready', 30))


def fake_run(answers):
    """Stand-in for the host's docker: maps the command's first two words to (returncode, stdout, stderr)."""
    calls = []

    def run(*args):
        calls.append(args)
        code, out, err = answers[' '.join(args[:2])]
        return subprocess.CompletedProcess(args, code, out, err)
    return run, calls


class AskSite(unittest.TestCase):
    def test_asks_the_app_container_directly_not_through_compose(self):
        run, calls = fake_run({'docker ps': (0, 'abc123\n', ''),
                               'docker exec': (0, '{"ready": false, "reasons": []}\n200', '')})
        self.assertEqual(module.ask_site(run), ('answer', {'ready': False, 'reasons': []}))
        self.assertTrue(all(c[:2] != ('docker', 'compose') for c in calls))
        self.assertEqual(calls[1][:3], ('docker', 'exec', 'abc123'))
        self.assertIn('http://localhost/api/v1/health/deploy-ready', calls[1])

    def test_no_running_app_means_nothing_to_cut_off(self):
        run, _ = fake_run({'docker ps': (0, '', '')})
        self.assertEqual(module.ask_site(run)[0], 'go')

    def test_reads_the_status_the_site_gave(self):
        for out, kind in [('not found\n404', 'go'), ('bad gateway\n502', 'down'), ('000', 'down')]:
            run, _ = fake_run({'docker ps': (0, 'abc\n', ''), 'docker exec': (7 if out == '000' else 0, out, '')})
            self.assertEqual(module.ask_site(run)[0], kind, out)

    def test_docker_trouble_or_a_garbled_answer_cannot_look(self):
        for answers in [{'docker ps': (1, '', 'permission denied')},
                        {'docker ps': (0, 'abc\n', ''), 'docker exec': (126, '', 'OCI runtime exec failed')},
                        {'docker ps': (0, 'abc\n', ''), 'docker exec': (0, '<html>\n200', '')},
                        {'docker ps': (0, 'abc\n', ''), 'docker exec': (0, '{"ok": true}\n200', '')}]:
            run, _ = fake_run(answers)
            with self.assertRaises(module.CantLook):
                module.ask_site(run)

    def test_only_unstable_holds_and_a_newer_candidate_cancels_the_wait(self):
        workflow = (ROOT/'.github/workflows/deploy.yaml').read_text()
        hold = workflow.split('  hold:\n', 1)[1].split('\n  deploy:\n', 1)[0]
        self.assertIn("github.event_name == 'repository_dispatch' || inputs.environment == 'unstable'", hold)
        self.assertIn('group: hold-unstable', hold)
        # Environment-scoped settings (DEPLOY_ENABLED, droplet secrets) are only visible inside the environment.
        self.assertIn('environment: unstable', hold)
        self.assertNotIn('vars.', hold.split('steps:', 1)[0])
        self.assertIn('cancel-in-progress: true', hold)
        self.assertIn('scripts/wait-until-deploy-ready.py', hold)
        deploy = workflow.split('\n  deploy:\n', 1)[1]
        self.assertIn("needs.hold.result == 'skipped'", deploy)  # production never waits
        self.assertIn('cancel-in-progress: false', deploy)


if __name__ == '__main__':
    unittest.main()
