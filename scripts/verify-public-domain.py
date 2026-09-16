"""Verify the public nested unstable boundary without exposing gate secrets."""

from hashlib import sha256
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from env_file import read_env
from environment_topology import load_topology


ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def status(opener, url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    request = Request(url, headers={'User-Agent': 'launch-lms-domain-verifier/1', **(headers or {})})
    try:
        with opener.open(request, timeout=15) as response:
            return response.status, response.headers.get('Location', '')
    except HTTPError as exc:
        return exc.code, exc.headers.get('Location', '')


def verify() -> None:
    env = read_env(ROOT / '.env')
    topology = load_topology(ROOT / 'deploy/environments/launch-lms.yaml')
    target = topology['application']['unstable']['base_domain']
    legacy = urlparse(topology['operations']['public_url']).hostname or ''
    if env.get('LAUNCHLMS_DOMAIN') != target:
        print('Nested unstable domain is not active; public cutover verification skipped.')
        return
    password_hash = env.get('UNSTABLE_HTTP_PASSWORD_HASH', '')
    if not password_hash:
        raise ValueError('Unstable HTTP gate hash is required')
    session = sha256(f'{target}\0{password_hash}'.encode()).hexdigest()
    direct = build_opener(NoRedirect(), HTTPCookieProcessor(CookieJar()))
    authenticated_headers = {'Cookie': f'launchlms_unstable_gate={session}'}

    unauthenticated_status, _ = status(direct, f'https://{target}/')
    if unauthenticated_status != 401:
        raise ValueError(f'Nested unstable access gate returned HTTP {unauthenticated_status}, expected 401')
    for host, path in ((target, '/login'), (f'life2launch.{target}', '/login')):
        response_status, _ = status(direct, f'https://{host}{path}', authenticated_headers)
        if response_status != 200:
            raise ValueError(f'Authenticated public route {host}{path} returned HTTP {response_status}')
    legacy_status, location = status(direct, f'https://{legacy}/')
    if legacy_status not in (301, 308) or location != f'https://{target}/':
        raise ValueError('Legacy operations-domain apex does not redirect to nested unstable')
    print('Verified nested unstable TLS, access gate, apex and tenant routing, and legacy redirect.')


if __name__ == '__main__':
    verify()
