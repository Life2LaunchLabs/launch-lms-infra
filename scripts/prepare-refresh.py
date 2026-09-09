import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tarfile
from urllib.parse import urlsplit, urlunsplit
from env_file import read_env

snapshot = Path(sys.argv[1]).resolve()
stamp = sys.argv[2]
assert re.fullmatch(r'[0-9]+', stamp)
assert Path('.deployment-environment').read_text().strip() == 'unstable'
meta = json.loads((snapshot/'snapshot.json').read_text())
assert meta['storage'] == 'filesystem'
for name in ('database.dump', 'content.tar.gz', 'release.json'):
    with (snapshot/name).open('rb') as file:
        assert hashlib.file_digest(file, 'sha256').hexdigest() == meta['files'][name], f'Snapshot checksum mismatch: {name}'
# Reject archive traversal and links before tar writes into the new content volume.
with tarfile.open(snapshot/'content.tar.gz') as archive:
    for member in archive:
        assert not member.name.startswith('/') and '..' not in Path(member.name).parts
        assert member.isfile() or member.isdir(), 'Snapshot archive must contain only regular files/directories'
env = read_env(Path('.env'))
source, target = meta['source_domain'], env['LAUNCHLMS_DOMAIN']
assert source != target and not target.endswith('.'+source) and not source.endswith('.'+target), 'Separate domains required'
assert hashlib.sha256(env['LAUNCHLMS_AUTH_JWT_SECRET_KEY'].encode()).hexdigest() != meta['jwt_fingerprint'], 'Production JWT key must not be reused'
url = urlsplit(env['LAUNCHLMS_SQL_CONNECTION_STRING'])
assert url.hostname == 'db'
database = 'launchlms_refresh_'+stamp
env['LAUNCHLMS_SQL_CONNECTION_STRING'] = urlunsplit(url._replace(path='/'+database))
env['CONTENT_VOLUME_NAME'] = 'launch-lms-unstable-content-'+stamp
env['LAUNCHLMS_AUTH_JWT_SECRET_KEY'] = secrets.token_urlsafe(48)
# Avoid interpolating nested variables when rewriting this installation's env.
for key, value in env.items():
    assert not any(c in value for c in "\n\r'"), f'Unsupported dotenv value for {key}'
    assert '${' not in value, f'Expand dotenv references before refresh: {key}'
os.umask(0o077)
state = Path('.deploy-state')
state.mkdir(exist_ok=True)
(state/'refresh.env').write_text(''.join(f"{k}='{v}'\n" for k,v in env.items()))
(state/'refresh.compose.json').write_text(json.dumps({'services': {service: {'env_file': [str((state/'refresh-app.env').resolve())]} for service in ('migrate', 'launch-lms')}}))
(state/'refresh-plan.json').write_text(json.dumps({'database': database, 'volume': env['CONTENT_VOLUME_NAME'], 'source_domain': source, 'target_domain': target, 'snapshot': meta}, indent=2)+'\n')
