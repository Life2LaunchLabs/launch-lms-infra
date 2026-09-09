import json
from pathlib import Path
import re
import shlex
import sys

REPOSITORY = 'ghcr.io/life2launchlabs/launch-lms'


def validate(lock, environment):
    if environment not in ('production', 'unstable'):
        raise ValueError('Invalid environment')
    digest = lock.get('image_digest', '')
    commit = lock.get('commit_sha', '')
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
        raise ValueError('Release must pin a full SHA256 image digest')
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Release must identify its full source commit')
    if lock.get('image_repository') != REPOSITORY or lock.get('image_ref') != f'{REPOSITORY}@{digest}':
        raise ValueError('Unexpected image repository or reference')
    branch = 'main' if environment == 'production' else 'dev'
    if lock.get('source_branch') != branch:
        raise ValueError(f'{environment} requires a {branch} candidate')
    if environment == 'production' and not re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', lock.get('version', '')):
        raise ValueError('Production requires a stable version')
    return lock


def main():
    data = validate(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2])
    values = {'LAUNCHLMS_IMAGE': data['image_ref'], 'LAUNCHLMS_IMAGE_DIGEST': data['image_digest'],
        'LAUNCHLMS_RELEASE_VERSION': data['version'], 'LAUNCHLMS_RELEASE_COMMIT_SHA': data['commit_sha']}
    for key, value in values.items():
        print(f'export {key}={shlex.quote(value)}')


if __name__ == '__main__':
    main()
