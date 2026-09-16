"""Attach review evidence to Jira; Merge status is the human approval gate."""
import base64
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.request import Request, urlopen
from uuid import uuid4

import sys
PLATFORM_ROOT = Path('/opt/platform') if Path('/opt/platform').exists() else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLATFORM_ROOT))
from services.orchestrator.manifest import load_project

ROOT = Path.home() / 'workspaces'
PROPERTY = 'launch-symphony-review'
MANIFEST = load_project(os.environ.get('OPERATIONS_PROJECT_ID', 'launch-lms'), PLATFORM_ROOT)
REPO = MANIFEST.repository
REQUIRED = set(MANIFEST.data['required_checks'])
ISSUE = re.compile(r'^' + re.escape(MANIFEST.data['tracker']['delivery_project']) + r'-\d+$')


def text_of(node):
    if isinstance(node, dict):
        return node.get('text', '') + '\n'.join(text_of(x) for x in node.get('content', []))
    return ''


def evidence_paths(workspace, paths):
    if not paths or len(paths) > 12:
        raise ValueError('Provide 1-12 evidence files')
    result = []
    for value in paths:
        path = (workspace / value).resolve()
        if not path.is_relative_to(workspace.resolve()) or not path.is_file():
            raise ValueError('Evidence must be a file inside the task workspace')
        if path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webm', '.mp4', '.md', '.txt', '.pdf'}:
            raise ValueError('Unsupported evidence format')
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('Each evidence file must be at most 20 MiB')
        result.append(path)
    return result


def jira(method, path, body=None, headers=None):
    auth = base64.b64encode((os.environ['JIRA_EMAIL'] + ':' + os.environ['JIRA_API_TOKEN']).encode()).decode()
    data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
    request = Request(os.environ['JIRA_BASE_URL'].rstrip('/') + '/rest/api/3/' + path,
                      data=data, method=method, headers={'Authorization': 'Basic ' + auth,
                      'Accept': 'application/json', 'Content-Type': 'application/json', **(headers or {})})
    with urlopen(request, timeout=45) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def comment(issue, text):
    return jira('POST', f'issue/{issue}/comment', {'body': {'type': 'doc', 'version': 1,
        'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}]}})


def transition(issue, name):
    transitions = jira('GET', f'issue/{issue}/transitions')['transitions']
    match = next((x for x in transitions if x['to']['name'] == name), None)
    if not match:
        raise ValueError('Missing Jira transition to ' + name)
    jira('POST', f'issue/{issue}/transitions', {'transition': {'id': match['id']}})


def labels(issue, add=(), remove=()):
    jira('PUT', f'issue/{issue}', {'update': {'labels': [{'add': x} for x in add] + [{'remove': x} for x in remove]}})


def pr_info(number):
    raw = subprocess.check_output(['gh', 'pr', 'view', str(number), '--repo', REPO, '--json',
        'number,url,state,isDraft,baseRefName,headRefOid,statusCheckRollup'], text=True)
    return json.loads(raw)


def check_pr(pr, sha):
    if pr['state'] != 'OPEN' or pr['isDraft'] or pr['baseRefName'] != 'dev' or pr['headRefOid'] != sha:
        raise ValueError('PR must be open, ready, target dev and match reviewed SHA')
    checks = {x.get('name', x.get('context')): x for x in pr['statusCheckRollup']}
    for name in REQUIRED:
        if checks.get(name, {}).get('conclusion') != 'SUCCESS':
            raise ValueError('Required check has not succeeded: ' + name)
    for name, check in checks.items():
        if name and 'browser' in name.lower() and check.get('conclusion') != 'SUCCESS':
            raise ValueError('Browser verification has not succeeded')


def upload(issue, path):
    boundary = 'symphony-' + uuid4().hex
    filename = re.sub(r'[^A-Za-z0-9._-]', '_', path.name)
    data = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: {mimetypes.guess_type(filename)[0] or "application/octet-stream"}\r\n\r\n').encode()
    data += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    return jira('POST', f'issue/{issue}/attachments', data,
                {'Content-Type': 'multipart/form-data; boundary=' + boundary, 'X-Atlassian-Token': 'no-check'})


def submit(path):
    request = json.loads(path.read_text())
    if path.is_symlink():
        raise ValueError('Manifest must not be a symlink')
    issue = request['issue']
    if not ISSUE.fullmatch(issue) or path.parent.name != issue:
        raise ValueError('Issue must match the task workspace')
    sha = request['sha']
    if not re.fullmatch(r'[a-f0-9]{40}', sha):
        raise ValueError('Full commit SHA required')
    if request.get('synthetic_evidence_only') is not True:
        raise ValueError('Evidence must be confirmed free of private learner data and secrets')
    state = jira('GET', f'issue/{issue}?fields=status,issuetype,labels')['fields']
    if state['status']['name'] == 'In Review':
        record = jira('GET', f'issue/{issue}/properties/{PROPERTY}')['value']
        if record['sha'] == sha and record['pr'] == int(request['pr']):
            path.rename(path.with_name('.symphony-review-submitted.json'))
            return
    if state['issuetype']['subtask'] or state['status']['name'] != 'In Progress' or 'symphony' not in state['labels']:
        raise ValueError('Only active opted-in parent tasks may request handoff')
    pr = pr_info(int(request['pr']))
    check_pr(pr, sha)
    paths = evidence_paths(path.parent, request['files'])
    workflow_metadata_path = Path.home() / 'rendered-workflow.json'
    workflow_metadata = json.loads(workflow_metadata_path.read_text()) if workflow_metadata_path.exists() else {}
    if not any(p.suffix == '.md' for p in paths):
        raise ValueError('A Markdown review report is required')
    if request.get('ui_change') and not any(p.suffix.lower() in {'.png', '.jpg', '.jpeg'} for p in paths):
        raise ValueError('UI review requires screenshots')
    progress = path.with_name('.symphony-review-upload.json')
    saved = json.loads(progress.read_text()) if progress.exists() else {}
    if saved.get('sha') != sha:
        saved = {'sha': sha, 'attachments': {}}
    for file in paths:
        relative = str(file.relative_to(path.parent))
        if relative not in saved['attachments']:
            saved['attachments'][relative] = upload(issue, file)
            progress.write_text(json.dumps(saved))
    links = ['%s: %s' % (name, ', '.join(x['content'] for x in entries)) for name, entries in saved['attachments'].items()]
    policy_line = ('\nPolicy commit/hash: ' + workflow_metadata.get('product_commit', 'unknown') +
                   ' / ' + workflow_metadata.get('rendered_workflow_sha256', 'unknown'))
    message = ('Ready for owner review — NOT merged or deployed.\n' + str(request['summary']) +
               '\nPR: ' + pr['url'] + '\nReviewed commit: ' + sha + '\nEvidence attached:\n' + '\n'.join(links) +
               policy_line +
               '\n\nReview the report/screenshots and PR. Move this issue to Merge to approve this exact revision.' +
               '\n\nFor changes, describe them and move this issue to To Do. Done remains your final product signoff.')
    check_pr(pr_info(int(request['pr'])), sha)
    current = jira('GET', f'issue/{issue}?fields=status,labels')['fields']
    if current['status']['name'] != 'In Progress' or 'symphony' not in current['labels']:
        raise ValueError('Owner changed task state during evidence upload')
    posted = comment(issue, message)
    record = {'sha': sha, 'pr': pr['number'], 'submitted_at': posted['created'], 'comment_id': posted['id'],
              'product_policy_commit': workflow_metadata.get('product_commit'),
              'rendered_workflow_sha256': workflow_metadata.get('rendered_workflow_sha256')}
    jira('PUT', f'issue/{issue}/properties/{PROPERTY}', record)
    transition(issue, 'In Review')
    path.rename(path.with_name('.symphony-review-submitted.json'))
    print('Evidence submitted for ' + issue, flush=True)


def main():
    while True:
        if not (Path.home() / 'PAUSED').exists():
            for path in ROOT.glob('BOT-*/.symphony-review-request.json'):
                try:
                    submit(path)
                except Exception as error:
                    print('Review handoff failed for ' + path.parent.name + ': ' + type(error).__name__, flush=True)
        time.sleep(30)


if __name__ == '__main__':
    main()
