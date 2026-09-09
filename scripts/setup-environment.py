"""Interactive first installation; secrets are written privately, never printed."""
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

spec = importlib.util.spec_from_file_location('release_env', Path(__file__).with_name('release-env.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if len(sys.argv) not in (2, 3) or sys.argv[1] not in ('production', 'unstable'):
    raise SystemExit('Usage: bash setup.sh production | bash setup.sh unstable /absolute/path/candidate.json')
environment = sys.argv[1]
if environment == 'production' and len(sys.argv) != 2:
    raise SystemExit('Production setup uses the checked-in release.lock.json')
source = Path(sys.argv[2]) if len(sys.argv) == 3 else Path('release.lock.json')
lock = module.validate(json.loads(source.read_text()), environment)
domain = input('Domain (owned DNS zone, no scheme or wildcard): ').strip().lower()
if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
    raise SystemExit('Invalid DNS hostname')
if environment == 'unstable':
    production = input('Production domain (must be a separate domain): ').strip().lower()
    if not production or domain == production or domain.endswith('.'+production) or production.endswith('.'+domain):
        raise SystemExit('Use a separate test domain, outside the production cookie domain')
values = {
    'LAUNCHLMS_SITE_NAME': 'Launch LMS — Unstable' if environment == 'unstable' else 'Launch LMS',
    'LAUNCHLMS_DOMAIN': domain, 'LAUNCHLMS_FRONTEND_DOMAIN': domain,
    'LAUNCHLMS_ALLOWED_ORIGINS': 'https://'+domain,
    'LAUNCHLMS_ALLOWED_REGEXP': r'^https://([a-z0-9-]+\.)?'+re.escape(domain)+'$',
    'LAUNCHLMS_COOKIE_DOMAIN': domain, 'NEXT_PUBLIC_LAUNCHLMS_DOMAIN': domain,
    'NEXT_PUBLIC_LAUNCHLMS_TOP_DOMAIN': domain,
    'NEXT_PUBLIC_LAUNCHLMS_API_URL': '',
    'NEXT_PUBLIC_LAUNCHLMS_BACKEND_URL': f'https://{domain}/',
    'LAUNCHLMS_INTERNAL_API_URL': 'http://localhost/api/v1/',
    'LAUNCHLMS_INTERNAL_BACKEND_URL': 'http://localhost:9000',
    'NEXT_PUBLIC_LAUNCHLMS_DEFAULT_ORG': 'life2launch',
    'NEXT_PUBLIC_COLLAB_URL': f'wss://{domain}/collab',
    'LAUNCHLMS_ENV': 'prod', 'LAUNCHLMS_DEVELOPMENT_MODE': 'false',
    'LAUNCHLMS_AUTH_JWT_SECRET_KEY': secrets.token_urlsafe(48),
    'COLLAB_INTERNAL_KEY': secrets.token_urlsafe(48),
    'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
    'LAUNCHLMS_INITIAL_ADMIN_EMAIL': input('Initial administrator email: ').strip(),
    'LAUNCHLMS_INITIAL_ADMIN_PASSWORD': getpass.getpass('Initial administrator password (12+ characters): '),
    'DO_AUTH_TOKEN': getpass.getpass('DigitalOcean DNS token: '),
    'LAUNCHLMS_CONTENT_DELIVERY_TYPE': 'filesystem',
    'LAUNCHLMS_REDIS_CONNECTION_STRING': 'redis://redis:6379/0',
}
if len(values['LAUNCHLMS_INITIAL_ADMIN_PASSWORD']) < 12 or not values['DO_AUTH_TOKEN']:
    raise SystemExit('An administrator password and DNS token are required')
values['LAUNCHLMS_SQL_CONNECTION_STRING'] = f"postgresql+psycopg2://launchlms:{values['POSTGRES_PASSWORD']}@db:5432/launchlms"
if environment == 'unstable':
    user = input('Shared tester HTTP username: ').strip()
    password = getpass.getpass('Shared tester HTTP password (12+ characters): ')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', user) or len(password) < 12:
        raise SystemExit('Choose a simple username and a password of at least 12 characters')
    # Caddy accepts the password from stdin; it is absent from process arguments.
    result = subprocess.run(['docker', 'run', '--rm', '-i', 'caddy:2', 'caddy', 'hash-password', '--algorithm', 'bcrypt'],
        input=password+'\n', text=True, capture_output=True, check=True)
    hashes = re.findall(r'\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}', result.stdout)
    if not hashes:
        raise SystemExit('Could not create tester password hash')
    values.update(UNSTABLE_HTTP_USER=user, UNSTABLE_HTTP_PASSWORD_HASH=hashes[-1])
# Preserve the supported configuration template and populate its blank defaults.
lines = []
remaining = dict(values)
for line in Path('.env.example').read_text().splitlines():
    key = line.split('=', 1)[0]
    if key in remaining:
        value = remaining.pop(key)
        if any(c in value for c in "\n\r'"):
            raise SystemExit(f'Unsupported quote/newline in {key}')
        line = f"{key}='{value}'"
    lines.append(line)
for key, value in remaining.items():
    if any(c in value for c in "\n\r'"):
        raise SystemExit(f'Unsupported quote/newline in {key}')
    lines.append(f"{key}='{value}'")
os.umask(0o077)
Path('.env').write_text('\n'.join(lines)+'\n')
Path('.deployment-environment').write_text(environment+'\n')
Path('.deploy-state').mkdir(exist_ok=True)
if environment == 'unstable':
    Path('.deploy-state/unstable.lock.json').write_text(json.dumps(lock, indent=2)+'\n')
print('Configuration saved privately. Each environment uses its own secrets.')
