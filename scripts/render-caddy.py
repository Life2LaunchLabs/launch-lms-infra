from pathlib import Path
from env_file import read_env
from environment_topology import load_topology
import hashlib
import re
from urllib.parse import urlparse

env = read_env(Path('.env'))
domain = env.get('LAUNCHLMS_DOMAIN', '')
domain_pattern = r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}'
if not re.fullmatch(domain_pattern, domain):
    raise ValueError('Use a DNS hostname without scheme, wildcard, or port')
config = Path('Caddyfile').read_text().replace('your.domain.com', domain)
deployment_environment = Path('.deployment-environment').read_text().strip()
routes = 'import launch_lms_proxy'
if deployment_environment == 'unstable':
    # Complete Basic authentication once, then use a secure domain cookie so
    # application Bearer tokens can occupy the Authorization header unchanged.
    user = env.get('UNSTABLE_HTTP_USER', '')
    password_hash = env.get('UNSTABLE_HTTP_PASSWORD_HASH', '')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', user) or not re.fullmatch(r'\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}', password_hash):
        raise ValueError('Unstable requires a tester HTTP username and bcrypt password hash')
    session = hashlib.sha256(f'{domain}\0{password_hash}'.encode()).hexdigest()
    cookie = f'launchlms_unstable_gate={session}; Domain=.{domain}; Path=/; Max-Age=43200; Secure; HttpOnly; SameSite=Lax'
    routes = f'''@unstable_session header_regexp Cookie "(^|; *)launchlms_unstable_gate={session}(;|$)"
    handle @unstable_session {{
        import launch_lms_proxy
    }}
    handle {{
        route {{
            basic_auth {{
                {user} {password_hash}
            }}
            header +Set-Cookie "{cookie}"
            request_header -Authorization
            import launch_lms_proxy
        }}
    }}'''
config = config.replace('__LAUNCHLMS_ROUTES__', routes)

# Before the nested unstable cutover, obtain and continuously renew its apex
# and wildcard certificate without sending application traffic to a runtime
# that is still configured for the legacy .dev hostname. This makes public
# DNS/TLS observable before any session or canonical-domain behavior changes.
topology_path = Path(__file__).resolve().parents[1] / 'deploy/environments/launch-lms.yaml'
topology = load_topology(topology_path)
operations_domain = urlparse(topology['operations']['public_url']).hostname or ''
nested_unstable_domain = topology['application']['unstable']['base_domain']
if (deployment_environment == 'unstable' and domain == operations_domain and
        domain != nested_unstable_domain and
        not topology['application']['unstable']['cutover_approved']):
    config += f'''

{nested_unstable_domain}, *.{nested_unstable_domain} {{
    tls {{
        dns digitalocean {{env.DO_AUTH_TOKEN}}
        resolvers 1.1.1.1 1.0.0.1
        propagation_delay 120s
        propagation_timeout 10m
    }}
    @domain_preflight path /.well-known/launch-lms-domain-preflight
    respond @domain_preflight 204
    respond 404
}}
'''
legacy = env.get('LAUNCHLMS_LEGACY_DOMAIN', '')
if legacy:
    if not re.fullmatch(domain_pattern, legacy):
        raise ValueError('Use a legacy DNS hostname without scheme, wildcard, or port')
    if legacy == domain or legacy.endswith('.' + domain) or domain.endswith('.' + legacy):
        raise ValueError('Legacy and current domains must be separate base domains')
    escaped = re.escape(legacy)
    config += f'''

{legacy}, *.{legacy} {{
    tls {{
        dns digitalocean {{env.DO_AUTH_TOKEN}}
        resolvers 1.1.1.1 1.0.0.1
        propagation_delay 120s
        propagation_timeout 10m
    }}
    @legacy_apex_or_www host {legacy} www.{legacy}
    redir @legacy_apex_or_www https://{domain}{{uri}} permanent
    @legacy_org header_regexp legacy_org Host ^([a-z0-9-]+)\\.{escaped}$
    redir @legacy_org https://{{re.legacy_org.1}}.{domain}{{uri}} permanent
}}
'''
Path('Caddyfile.active').write_text(config)
