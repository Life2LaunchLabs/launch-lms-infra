from pathlib import Path
from env_file import read_env
import re

env = read_env(Path('.env'))
domain = env.get('LAUNCHLMS_DOMAIN', '')
if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
    raise ValueError('Use a DNS hostname without scheme, wildcard, or port')
config = Path('Caddyfile').read_text().replace('your.domain.com', domain)
if Path('.deployment-environment').read_text().strip() == 'unstable':
    # Network isolation stops server-side integration writes; this gate limits
    # who can access the copied data. Credentials are installation-specific.
    user = env.get('UNSTABLE_HTTP_USER', '')
    password_hash = env.get('UNSTABLE_HTTP_PASSWORD_HASH', '')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', user) or not re.fullmatch(r'\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}', password_hash):
        raise ValueError('Unstable requires a tester HTTP username and bcrypt password hash')
    config = config.replace('    reverse_proxy', f'    basic_auth {{\n        {user} {password_hash}\n    }}\n    reverse_proxy')
Path('Caddyfile.active').write_text(config)
