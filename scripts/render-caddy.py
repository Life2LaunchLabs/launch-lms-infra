from pathlib import Path
from env_file import read_env
import hashlib
import re

env = read_env(Path('.env'))
domain = env.get('LAUNCHLMS_DOMAIN', '')
if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
    raise ValueError('Use a DNS hostname without scheme, wildcard, or port')
config = Path('Caddyfile').read_text().replace('your.domain.com', domain)
routes = 'import launch_lms_proxy'
if Path('.deployment-environment').read_text().strip() == 'unstable':
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
Path('Caddyfile.active').write_text(config)
