import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observe_unstable_deployment', ROOT/'scripts'/'observe-unstable-deployment.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SHA = 'a' * 40
DIGEST = 'sha256:' + 'b' * 64


class ObserveUnstableDeployment(unittest.TestCase):
    def observe(self, source_branch):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'.deployment-environment').write_text('unstable\n')
            (root/'.deploy-state').mkdir()
            (root/'.deploy-state/deployed-release.json').write_text(json.dumps({
                'commit_sha': SHA, 'image_digest': DIGEST, 'build_run_id': '7',
                'image_ref': f'ghcr.io/life2launchlabs/launch-lms@{DIGEST}'}))
            answers = {'ps': 'c1', 'inspect': 'img', 'image': 'img', 'exec': json.dumps({'commit_sha': SHA, 'source_branch': source_branch}), 'git': 'c' * 40}
            module.ROOT, module.command = root, lambda *args: answers[args[1] if args[0] == 'docker' else 'git']
            return module.observe('1', '1')

    def test_an_image_built_on_dev_is_accepted(self):
        self.assertEqual(self.observe('dev')['source_sha'], SHA)

    def test_an_image_checked_in_devs_merge_queue_is_accepted(self):
        self.assertEqual(self.observe('gh-readonly-queue/dev/pr-415-16eac0f9')['source_sha'], SHA)

    def test_an_image_from_another_branch_is_refused(self):
        with self.assertRaises(ValueError):
            self.observe('feature/x')


if __name__ == '__main__':
    unittest.main()
