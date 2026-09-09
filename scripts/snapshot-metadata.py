import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from env_file import read_env

path = Path(sys.argv[1])
env = read_env(Path('.env'))
data = {'source_domain': env['LAUNCHLMS_DOMAIN'], 'created_at': datetime.now(timezone.utc).isoformat(),
    'jwt_fingerprint': hashlib.sha256(env['LAUNCHLMS_AUTH_JWT_SECRET_KEY'].encode()).hexdigest(),
    'storage': 'filesystem', 'files': {}}
for name in ('database.dump', 'content.tar.gz', 'release.json'):
    with (path/name).open('rb') as file:
        data['files'][name] = hashlib.file_digest(file, 'sha256').hexdigest()
(path/'snapshot.json').write_text(json.dumps(data, indent=2)+'\n')
