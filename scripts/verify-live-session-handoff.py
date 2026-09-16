"""Exercise the live host-only login handoff without logging credentials or tokens."""

from hashlib import sha256
from html.parser import HTMLParser
from http.cookiejar import Cookie, CookieJar
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener

from env_file import read_env
from environment_topology import load_topology


ROOT = Path(__file__).resolve().parents[1]
AUTH_COOKIE_NAMES = {'access_token_cookie', 'refresh_token_cookie'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class HandoffForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = ''
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if tag == 'form':
            self.action = values.get('action', '')
        elif tag == 'input' and values.get('name'):
            self.fields[values['name']] = values.get('value', '')


def cookie(name: str, value: str, domain: str, *, host_only: bool = False) -> Cookie:
    return Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=not host_only,
        domain_initial_dot=domain.startswith('.') and not host_only,
        path='/', path_specified=True, secure=True, expires=None, discard=True,
        comment=None, comment_url=None, rest={}, rfc2109=False,
    )


def request(opener, url: str, *, data: dict[str, str] | None = None,
            json_data: dict | None = None) -> tuple[int, dict, bytes]:
    if data is not None and json_data is not None:
        raise ValueError('Choose form data or JSON, not both')
    encoded = urlencode(data).encode() if data is not None else (
        json.dumps(json_data).encode() if json_data is not None else None
    )
    headers = {'User-Agent': 'launch-lms-live-handoff-verifier/1'}
    if data is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    elif json_data is not None:
        headers['Content-Type'] = 'application/json'
    web_request = Request(url, data=encoded, headers=headers, method='POST' if encoded is not None else 'GET')
    try:
        with opener.open(web_request, timeout=20) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers), error.read()


def auth_domains(jar: CookieJar) -> set[str]:
    return {item.domain.lstrip('.') for item in jar if item.name in AUTH_COOKIE_NAMES}


def validated_route_redirect(current_url: str, location: str, expected_host: str,
                             expected_path: str) -> str:
    destination = urljoin(current_url, location)
    parsed = urlparse(destination)
    if (parsed.scheme != 'https' or parsed.hostname != expected_host or
            parsed.path.rstrip('/') != expected_path.rstrip('/') or
            any(name in parsed.query for name in ('ticket=', 'access_token=', 'refresh_token='))):
        raise ValueError('Handoff route returned an unsafe canonical redirect')
    return destination


def validated_completion_redirect(current_url: str, location: str,
                                  expected_host: str) -> str:
    destination = urljoin(current_url, location)
    parsed = urlparse(destination)
    if (parsed.scheme != 'https' or parsed.hostname != expected_host or
            parsed.port not in (None, 443) or parsed.path != '/account' or
            parsed.params or parsed.query or parsed.fragment or
            parsed.username is not None or parsed.password is not None):
        raise ValueError('Handoff completion did not redirect to the reviewed target path')
    return destination


def verify() -> None:
    env = read_env(ROOT / '.env')
    topology = load_topology(ROOT / 'deploy/environments/launch-lms.yaml')
    target = topology['application']['unstable']['base_domain']
    source = f"life2launch.{target}"
    if env.get('LAUNCHLMS_DOMAIN') != target or env.get('LAUNCHLMS_COOKIE_SCOPE') != 'host-only':
        raise ValueError('Live handoff requires the reviewed nested host-only runtime')
    jwt_secret = env.get('LAUNCHLMS_AUTH_JWT_SECRET_KEY', '')
    email = 'acceptance-session-handoff@invalid.example'
    password = sha256(f'live-handoff\0{jwt_secret}'.encode()).hexdigest() + 'A1!'
    password_hash = env.get('UNSTABLE_HTTP_PASSWORD_HASH', '')
    if len(jwt_secret) < 32 or not password_hash:
        raise ValueError('Live handoff acceptance credentials are unavailable')

    jar = CookieJar()
    gate = sha256(f'{target}\0{password_hash}'.encode()).hexdigest()
    jar.set_cookie(cookie('launchlms_unstable_gate', gate, f'.{target}'))
    jar.set_cookie(cookie('launchlms_current_orgslug', 'legacy', '.life2launch.app'))
    opener = build_opener(NoRedirect(), HTTPCookieProcessor(jar))

    signup_status, _, signup_body = request(
        opener, f'https://{source}/api/auth/signup/welcome',
        json_data={'email': email, 'password': password, 'quiz_result': None},
    )
    if signup_status == 200:
        if not json.loads(signup_body).get('user'):
            raise ValueError('Synthetic acceptance account response omitted the user')
        signup_logout, _, _ = request(opener, f'https://{source}/api/auth/logout', data={})
        if signup_logout != 200:
            raise ValueError('Could not clear the synthetic signup session before login acceptance')
    elif signup_status != 409:
        raise ValueError(f'Synthetic acceptance account preparation returned HTTP {signup_status}')

    login_status, _, login_body = request(
        opener, f'https://{source}/api/auth/login', data={'username': email, 'password': password}
    )
    if login_status != 200 or not json.loads(login_body).get('user'):
        raise ValueError(f'Live source-host login returned HTTP {login_status}')
    if source not in auth_domains(jar) or target in auth_domains(jar):
        raise ValueError('Login cookies were not isolated to the source host')

    start_url = f'https://{target}/api/auth/handoff/start?' + urlencode({
        'source': source, 'return': '/account',
    })
    start_status, start_headers, _ = request(opener, start_url)
    issue_url = start_headers.get('Location', '')
    if start_status not in (302, 307) or urlparse(issue_url).hostname != source:
        raise ValueError('Handoff start did not redirect to the authenticated source host')

    issue_status, issue_headers, issue_body = request(opener, issue_url)
    if issue_status in (301, 302, 307, 308):
        issue_url = validated_route_redirect(
            issue_url, issue_headers.get('Location', ''), source, '/api/auth/handoff/issue'
        )
        issue_status, issue_headers, issue_body = request(opener, issue_url)
    if issue_status != 200 or 'text/html' not in issue_headers.get('Content-Type', ''):
        raise ValueError(f'Handoff ticket issue returned HTTP {issue_status}')
    form = HandoffForm()
    form.feed(issue_body.decode())
    expected_fields = {'ticket', 'state', 'return_path'}
    if set(form.fields) != expected_fields or urlparse(form.action).hostname != target:
        raise ValueError('Handoff response did not contain the host-bound POST form')
    if any(name in urlparse(issue_url).query for name in ('ticket=', 'access_token=', 'refresh_token=')):
        raise ValueError('Handoff secret appeared in a URL')

    complete_status, complete_headers, _ = request(opener, form.action, data=form.fields)
    if complete_status in (301, 302, 307, 308):
        form.action = validated_route_redirect(
            form.action, complete_headers.get('Location', ''), target,
            '/api/auth/handoff/complete',
        )
        complete_status, complete_headers, _ = request(opener, form.action, data=form.fields)
    raw_account_url = urljoin(form.action, complete_headers.get('Location', ''))
    if complete_status != 303:
        destination = urlparse(raw_account_url)
        raise ValueError(
            'Handoff completion did not redirect to the reviewed target path: '
            f'HTTP {complete_status}, destination {destination.hostname or "missing"}'
            f'{destination.path or "/"}'
        )
    account_url = validated_completion_redirect(
        form.action, complete_headers.get('Location', ''), target
    )
    account_status, _, _ = request(opener, account_url)
    if account_status != 200:
        raise ValueError(f'Authenticated target account returned HTTP {account_status}')
    if auth_domains(jar) != {source, target}:
        raise ValueError('Handoff did not retain isolated source and target auth cookies')
    if any(item.name == 'launchlms_current_orgslug' and item.domain.lstrip('.') == 'life2launch.app' for item in jar):
        raise ValueError('Legacy parent-domain routing cookie was not expired')

    replay_status, _, _ = request(opener, form.action, data=form.fields)
    if replay_status != 401:
        raise ValueError(f'Handoff ticket replay returned HTTP {replay_status}, expected 401')
    target_logout, _, _ = request(opener, f'https://{target}/api/auth/logout', data={})
    if target_logout != 200 or auth_domains(jar) != {source}:
        raise ValueError('Target logout did not preserve source-host isolation')
    source_logout, _, _ = request(opener, f'https://{source}/api/auth/logout', data={})
    if source_logout != 200 or auth_domains(jar):
        raise ValueError('Source logout did not clear the final host-only session')
    print('Verified live login, one-use handoff, legacy-cookie expiry, host isolation, and logout.')


if __name__ == '__main__':
    verify()
