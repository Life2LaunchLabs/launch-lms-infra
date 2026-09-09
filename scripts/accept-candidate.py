"""Persist validated dispatch evidence on the unstable host under deploy lock."""
import base64
import importlib.util
import json
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location('release_env', Path(__file__).with_name('release-env.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if Path('.deployment-environment').read_text().strip() != 'unstable':
    raise ValueError('Candidate dispatch is only valid for unstable')
data = module.validate(json.loads(base64.b64decode(os.environ['CANDIDATE_BASE64'], validate=True)), 'unstable')
run_id = int(data['build_run_id'])
path = Path('.deploy-state/unstable.lock.json')
if path.exists() and run_id < int(json.loads(path.read_text()).get('build_run_id', 0)):
    raise ValueError('Refusing an out-of-order unstable build; manually restore a lock for rollback')
path.parent.mkdir(exist_ok=True)
tmp = path.with_suffix('.tmp')
tmp.write_text(json.dumps(data, indent=2)+'\n')
tmp.replace(path)
