"""Verify the nested unstable DNS/TLS endpoint when Caddy rendered it."""

from pathlib import Path
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from environment_topology import load_topology


MARKER = '@domain_preflight path /.well-known/launch-lms-domain-preflight'


def configured_preflight_url(active_config: Path = Path('Caddyfile.active')) -> str | None:
    if not active_config.exists() or MARKER not in active_config.read_text():
        return None
    topology = load_topology(
        Path(__file__).resolve().parents[1] / 'deploy/environments/launch-lms.yaml'
    )
    domain = topology['application']['unstable']['base_domain']
    return f'https://{domain}/.well-known/launch-lms-domain-preflight'


def verify(url: str, attempts: int = 40, interval: int = 15) -> None:
    context = ssl.create_default_context()
    for attempt in range(1, attempts + 1):
        try:
            request = Request(url, method='GET', headers={'User-Agent': 'launch-lms-deploy-preflight/1'})
            with urlopen(request, timeout=10, context=context) as response:
                if response.status == 204:
                    print(f'Verified nested-domain DNS/TLS preflight: {url}')
                    return
                error = f'unexpected HTTP {response.status}'
        except HTTPError as exc:
            error = f'unexpected HTTP {exc.code}'
        except (TimeoutError, URLError) as exc:
            error = type(exc).__name__
        if attempt < attempts:
            print(f'Nested-domain preflight attempt {attempt}/{attempts} failed ({error}); retrying.')
            time.sleep(interval)
    raise SystemExit(f'Nested-domain DNS/TLS preflight failed after {attempts} attempts: {url}')


if __name__ == '__main__':
    preflight_url = configured_preflight_url()
    if preflight_url:
        verify(preflight_url)
    else:
        print('Nested-domain DNS/TLS preflight is not configured; skipping.')
