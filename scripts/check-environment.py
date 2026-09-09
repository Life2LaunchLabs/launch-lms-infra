from pathlib import Path
from env_file import read_env
from urllib.parse import urlparse

env = read_env(Path('.env'))
if env.get('LAUNCHLMS_DEVELOPMENT_MODE') != 'false' or env.get('LAUNCHLMS_ENV') != 'prod':
    raise ValueError('Both environments must use production runtime mode')
if env.get('LAUNCHLMS_INTERNAL_BACKEND_URL') != 'http://localhost:9000':
    raise ValueError('Server-side auth must use the container-local FastAPI endpoint')
if not env.get('NEXT_PUBLIC_LAUNCHLMS_DEFAULT_ORG'):
    raise ValueError('Set the default organization slug so apex links stay on the main domain')
redis_url = urlparse(env.get('LAUNCHLMS_REDIS_CONNECTION_STRING', ''))
if redis_url.path not in ('', '/') and not redis_url.path[1:].isdigit():
    raise ValueError('Redis database must be numeric, for example redis://redis:6379/0')
if len(env.get('LAUNCHLMS_AUTH_JWT_SECRET_KEY', '')) < 32:
    raise ValueError('Generate a separate JWT secret for this installation')
if Path('.deployment-environment').read_text().strip() == 'unstable':
    if urlparse(env.get('LAUNCHLMS_SQL_CONNECTION_STRING', '')).hostname != 'db':
        raise ValueError('Unstable must use its local isolated database')
    if urlparse(env.get('LAUNCHLMS_REDIS_CONNECTION_STRING', '')).hostname != 'redis':
        raise ValueError('Unstable must use its local isolated Redis')
    if env.get('LAUNCHLMS_CONTENT_DELIVERY_TYPE') != 'filesystem':
        raise ValueError('Unstable requires an independent filesystem content copy')
    forbidden = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CLIENT_ID')
    allowed = {'LAUNCHLMS_AUTH_JWT_SECRET_KEY', 'COLLAB_INTERNAL_KEY', 'POSTGRES_PASSWORD',
        'LAUNCHLMS_INITIAL_ADMIN_PASSWORD', 'DO_AUTH_TOKEN', 'UNSTABLE_HTTP_PASSWORD_HASH'}
    for key, value in env.items():
        if value and any(word in key for word in forbidden) and key not in allowed:
            raise ValueError(f'Remove external integration credential from unstable: {key}')
