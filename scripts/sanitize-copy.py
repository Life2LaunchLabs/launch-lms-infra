"""Sanitize a freshly restored test database before any app can serve it."""
import os
import re
from urllib.parse import urlsplit, urlunsplit
from sqlalchemy import JSON, MetaData, String, create_engine, select, text


def rewrite(value, source, target):
    if isinstance(value, dict):
        return {key: rewrite(item, source, target) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item, source, target) for item in value]
    if not isinstance(value, str):
        return value
    # Only URL authorities; leave emails, password hashes, and unrelated hosts alone.
    pattern = r'(?:https?|wss?)://[^\s<>"\x27]+'
    def replace(match):
        url = urlsplit(match.group())
        host = url.hostname or ''
        if host == source or host.endswith('.'+source):
            if url.username or url.password:
                return ''  # Never retain embedded credentials to installation services.
            host = host[:-len(source)] + target
            return urlunsplit((url.scheme, host, url.path, url.query, url.fragment))
        return match.group()
    return re.sub(pattern, replace, value)


def sanitize(conn, source, target):
    metadata = MetaData()
    metadata.reflect(conn)
    tables = metadata.tables
    # Redis is never copied. JWT signing keys are replaced on every refresh.
    for name in ('apitoken', 'customdomain'):
        if name in tables:
            conn.execute(tables[name].delete())
    changes = {
        'ssoconnection': dict(enabled=False, provider_config={}, domains=[]),
        'paymentsconfig': dict(enabled=False, active=False, provider_config={}, provider_specific_id=None),
        'organization': dict(scripts={}),
        'organizationinvitation': dict(status='revoked', delivery_status='cancelled', invite_code_uuid=None),
        'user': dict(failed_login_attempts=0, locked_until=None, last_login_ip=None),
    }
    if 'organizationjoinlink' in tables:
        conn.execute(tables['organizationjoinlink'].delete())
    if 'guestsession' in tables:
        if conn.dialect.name == 'postgresql':
            conn.execute(text('TRUNCATE TABLE guestsession CASCADE'))
        else:
            conn.execute(tables['guestsession'].delete())
    for name, values in changes.items():
        if name in tables:
            table = tables[name]
            fields = {key: value for key, value in values.items() if key in table.c}
            if fields:
                conn.execute(table.update().values(**fields))
    for table in tables.values():
        keys = list(table.primary_key.columns)
        columns = [c for c in table.c if isinstance(c.type, (String, JSON)) and c.name not in ('password', 'email', 'token_hash')]
        if not keys or not columns:
            continue
        for row in conn.execute(select(table)).mappings():
            values = {}
            for column in columns:
                before = row[column.name]
                after = rewrite(before, source, target)
                if after != before:
                    values[column.name] = after
            if values:
                query = table.update()
                for key in keys:
                    query = query.where(key == row[key.name])
                conn.execute(query.values(**values))


if __name__ == '__main__':
    engine = create_engine(os.environ['LAUNCHLMS_SQL_CONNECTION_STRING'])
    if engine.url.host != 'db' or not re.fullmatch(r'launchlms_refresh_[0-9]+', engine.url.database or ''):
        raise SystemExit('Refusing to sanitize anything except a fresh local refresh database')
    source, target = os.environ['SOURCE_DOMAIN'], os.environ['TARGET_DOMAIN']
    if not source or not target or source == target or target.endswith('.'+source):
        raise SystemExit('A separate test domain is required')
    with engine.begin() as conn:
        sanitize(conn, source, target)
    print('Copied database sanitized; password hashes and learning data retained.')
