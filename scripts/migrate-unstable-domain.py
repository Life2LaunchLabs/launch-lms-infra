"""Apply or roll back the reviewed nested unstable domain configuration."""

import argparse
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlparse

from env_file import read_env
from environment_topology import load_topology


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / '.env'
BACKUP_PATH = ROOT / '.deploy-state/pre-nested-domain-cutover.env'
TOPOLOGY_PATH = ROOT / 'deploy/environments/launch-lms.yaml'


def replacement_values(topology: dict) -> dict[str, str]:
    domain = topology['application']['unstable']['base_domain']
    legacy = urlparse(topology['operations']['public_url']).hostname or ''
    app_zone = topology['dns']['app_zone']
    return {
        'LAUNCHLMS_DOMAIN': domain,
        'LAUNCHLMS_LEGACY_DOMAIN': legacy,
        'LAUNCHLMS_FRONTEND_DOMAIN': domain,
        'LAUNCHLMS_SSL': 'true',
        'LAUNCHLMS_ALLOWED_ORIGINS': f'https://{domain}',
        'LAUNCHLMS_ALLOWED_REGEXP': rf'^https://([a-z0-9-]+\.)?{re.escape(domain)}$',
        'LAUNCHLMS_COOKIE_DOMAIN': domain,
        'LAUNCHLMS_COOKIE_SCOPE': topology['application']['cookie_scope'],
        'NEXT_PUBLIC_LAUNCHLMS_DOMAIN': domain,
        'NEXT_PUBLIC_LAUNCHLMS_COOKIE_SCOPE': topology['application']['cookie_scope'],
        'NEXT_PUBLIC_LAUNCHLMS_LEGACY_COOKIE_DOMAIN': app_zone,
        'NEXT_PUBLIC_LAUNCHLMS_BACKEND_URL': f'https://{domain}/',
        'NEXT_PUBLIC_LAUNCHLMS_TOP_DOMAIN': domain,
        'NEXT_PUBLIC_LAUNCHLMS_HTTPS': 'true',
        'NEXT_PUBLIC_COLLAB_URL': f'wss://{domain}/collab',
    }


def write_replacements(path: Path, replacements: dict[str, str]) -> None:
    for key, value in replacements.items():
        if "'" in value or '\n' in value or '\r' in value:
            raise ValueError(f'Unsupported value for {key}')
    remaining = dict(replacements)
    lines = []
    for line in path.read_text().splitlines():
        key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else ''
        if key in remaining:
            line = f"{key}='{remaining.pop(key)}'"
        lines.append(line)
    lines.extend(f"{key}='{value}'" for key, value in remaining.items())
    mode = path.stat().st_mode & 0o777
    descriptor, temporary = tempfile.mkstemp(prefix='.env.cutover-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write('\n'.join(lines) + '\n')
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_context(topology: dict) -> tuple[str, str]:
    if (ROOT / '.deployment-environment').read_text().strip() != 'unstable':
        raise ValueError('Nested-domain migration is allowed only on the unstable installation')
    target = topology['application']['unstable']['base_domain']
    legacy = urlparse(topology['operations']['public_url']).hostname or ''
    if not target or not legacy or target == legacy:
        raise ValueError('Topology must define separate unstable and operations domains')
    return target, legacy


def apply() -> None:
    topology = load_topology(TOPOLOGY_PATH)
    target, legacy = validate_context(topology)
    if not topology['application']['session_handoff_verified']:
        raise ValueError('Session handoff evidence has not been approved')
    if not topology['application']['unstable']['cutover_approved']:
        raise ValueError('Nested unstable cutover has not been approved')
    current = read_env(ENV_PATH).get('LAUNCHLMS_DOMAIN')
    if current not in (legacy, target):
        raise ValueError('Current unstable domain does not match the reviewed migration boundary')
    BACKUP_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    if current == legacy and not BACKUP_PATH.exists():
        shutil.copyfile(ENV_PATH, BACKUP_PATH)
        os.chmod(BACKUP_PATH, 0o600)
    write_replacements(ENV_PATH, replacement_values(topology))
    print(f'Configured unstable runtime for {target}; rollback snapshot is retained.')


def rollback() -> None:
    topology = load_topology(TOPOLOGY_PATH)
    _, legacy = validate_context(topology)
    if not BACKUP_PATH.exists():
        raise ValueError('No pre-cutover environment snapshot is available')
    mode = ENV_PATH.stat().st_mode & 0o777
    shutil.copyfile(BACKUP_PATH, ENV_PATH)
    os.chmod(ENV_PATH, mode)
    if read_env(ENV_PATH).get('LAUNCHLMS_DOMAIN') != legacy:
        raise ValueError('Rollback snapshot does not contain the reviewed legacy domain')
    print(f'Restored unstable runtime configuration for {legacy}.')


def status() -> None:
    topology = load_topology(TOPOLOGY_PATH)
    target, legacy = validate_context(topology)
    current = read_env(ENV_PATH).get('LAUNCHLMS_DOMAIN', '')
    state = 'nested' if current == target else 'legacy' if current == legacy else 'unexpected'
    print(f'domain_state={state} rollback_available={str(BACKUP_PATH.exists()).lower()}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('apply', 'rollback', 'status'))
    arguments = parser.parse_args()
    {'apply': apply, 'rollback': rollback, 'status': status}[arguments.action]()
