import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT/'scripts'))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class DeploymentTests(unittest.TestCase):
    def lock(self, branch='dev'):
        repo = 'ghcr.io/life2launchlabs/launch-lms'
        digest = 'sha256:'+'a'*64
        return dict(image_repository=repo, image_digest=digest, image_ref=repo+'@'+digest,
            commit_sha='b'*40, source_branch=branch, version='v1.2.3', build_run_id='100')

    def test_environment_lock_cannot_cross_deploy(self):
        module = load('release-env')
        module.validate(self.lock(), 'unstable')
        module.validate(self.lock('main'), 'production')
        module.validate({**self.lock('main'), 'version': '1.2.3'}, 'production')
        for lock, env in [(self.lock(), 'production'), (self.lock('main'), 'unstable'),
                ({**self.lock(), 'image_digest':None}, 'unstable'),
                ({**self.lock(), 'image_ref':'untrusted:latest'}, 'unstable')]:
            with self.subTest(lock=lock, env=env), self.assertRaises(ValueError):
                module.validate(lock, env)

    def test_stale_dispatch_rejected_without_changing_lock(self):
        import base64
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp); (path/'.deployment-environment').write_text('unstable\n')
            (path/'.deploy-state').mkdir()
            lock = path/'.deploy-state/unstable.lock.json'
            lock.write_text(json.dumps(self.lock()))
            payload = {**self.lock(), 'build_run_id':'99'}
            result = subprocess.run([sys.executable, str(ROOT/'scripts/accept-candidate.py')], cwd=path,
                env={**os.environ, 'CANDIDATE_BASE64':base64.b64encode(json.dumps(payload).encode()).decode()}, capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual('100', json.loads(lock.read_text())['build_run_id'])

    def fixture(self, path, *, traversal=False):
        snap=path/'snapshot'; snap.mkdir()
        (path/'.deployment-environment').write_text('unstable')
        (path/'.env').write_text("LAUNCHLMS_DOMAIN=test.example.net\nLAUNCHLMS_AUTH_JWT_SECRET_KEY="+'t'*40+"\nLAUNCHLMS_SQL_CONNECTION_STRING=postgresql+psycopg2://launchlms:password@db:5432/launchlms\n")
        (snap/'database.dump').write_bytes(b'fixture')
        (snap/'release.json').write_text('{}')
        with tarfile.open(snap/'content.tar.gz', 'w:gz') as tar:
            member=tarfile.TarInfo('../escape' if traversal else './file.txt'); member.size=5
            tar.addfile(member, io.BytesIO(b'hello'))
        meta=dict(storage='filesystem',source_domain='production.example.org',created_at='test',jwt_fingerprint=hashlib.sha256(b'p'*40).hexdigest(),files={})
        for name in ('database.dump','content.tar.gz','release.json'):
            meta['files'][name]=hashlib.sha256((snap/name).read_bytes()).hexdigest()
        (snap/'snapshot.json').write_text(json.dumps(meta))
        return snap

    def test_refresh_prepares_new_data_and_rotates_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp); snap=self.fixture(path)
            before=(path/'.env').read_text()
            subprocess.run([sys.executable,str(ROOT/'scripts/prepare-refresh.py'),str(snap),'20260908120000'], cwd=path,check=True)
            self.assertEqual(before,(path/'.env').read_text())
            proposed=(path/'.deploy-state/refresh.env').read_text()
            self.assertIn('/launchlms_refresh_20260908120000',proposed)
            self.assertNotIn('t'*40,proposed)
            self.assertEqual(0o600,(path/'.deploy-state/refresh.env').stat().st_mode & 0o777)

    def test_bad_snapshot_and_production_target_rejected(self):
        for case in ('traversal','checksum','production','shared_key','shared_domain'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp); snap=self.fixture(path,traversal=case=='traversal')
                if case=='checksum': (snap/'database.dump').write_bytes(b'changed')
                if case=='production': (path/'.deployment-environment').write_text('production')
                if case=='shared_key': (path/'.env').write_text((path/'.env').read_text().replace('t'*40,'p'*40))
                if case=='shared_domain': (path/'.env').write_text((path/'.env').read_text().replace('test.example.net','dev.production.example.org'))
                result=subprocess.run([sys.executable,str(ROOT/'scripts/prepare-refresh.py'),str(snap),'20260908120000'],cwd=path,capture_output=True)
                self.assertNotEqual(0,result.returncode)
                self.assertFalse((path/'.deploy-state/refresh.env').exists())

    def test_edge_secrets_are_excluded_from_application_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            (path/'.env').write_text("DO_AUTH_TOKEN=private-dns-token\nUNSTABLE_HTTP_PASSWORD_HASH=private-hash\nLAUNCHLMS_AUTH_JWT_SECRET_KEY=app-key\nCOLLAB_INTERNAL_KEY=collab-key\nNEXT_PUBLIC_LAUNCHLMS_DOMAIN=test.example.net\n")
            subprocess.run([sys.executable,str(ROOT/'scripts/render-app-env.py')],cwd=path,check=True)
            result=(path/'.deploy-state/app.env').read_text()
            self.assertNotIn('private-dns-token',result)
            self.assertNotIn('private-hash',result)
            self.assertIn('app-key',result)
            self.assertIn('collab-key',result)

    def test_unstable_application_egress_requires_explicit_switch(self):
        isolated=(ROOT/'docker-compose.unstable.yml').read_text()
        opt_in=(ROOT/'docker-compose.unstable-app-egress.yml').read_text()
        loader=(ROOT/'scripts/load-release-env.sh').read_text()
        self.assertNotIn('launch-lms:\n    networks:',isolated)
        self.assertIn('launch-lms:\n    networks: [default, egress]',opt_in)
        self.assertIn("UNSTABLE_APP_EGRESS_ENABLED",loader)
        self.assertIn('docker-compose.unstable-app-egress.yml',loader)

    def test_unstable_gate_uses_shared_session_cookie_without_forwarding_basic_auth(self):
        password_hash = '$2a$14$' + 'a'*53
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            shutil.copy(ROOT/'Caddyfile', path/'Caddyfile')
            (path/'.deployment-environment').write_text('unstable\n')
            (path/'.env').write_text(
                'LAUNCHLMS_DOMAIN=test.example.net\n'
                'UNSTABLE_HTTP_USER=launch_testers\n'
                f'UNSTABLE_HTTP_PASSWORD_HASH={password_hash}\n'
            )
            subprocess.run([sys.executable,str(ROOT/'scripts/render-caddy.py')],cwd=path,check=True)
            result=(path/'Caddyfile.active').read_text()
            self.assertIn('handle @unstable_session', result)
            self.assertIn('Domain=.test.example.net', result)
            self.assertIn('request_header -Authorization', result)
            self.assertEqual(2, result.count('import launch_lms_proxy'))
            self.assertNotIn('__LAUNCHLMS_ROUTES__', result)

    def test_legacy_domain_redirects_apex_www_and_org_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            shutil.copy(ROOT/'Caddyfile', path/'Caddyfile')
            (path/'.deployment-environment').write_text('production\n')
            (path/'.env').write_text(
                'LAUNCHLMS_DOMAIN=new.example.net\n'
                'LAUNCHLMS_LEGACY_DOMAIN=old.example.org\n'
            )
            subprocess.run([sys.executable,str(ROOT/'scripts/render-caddy.py')],cwd=path,check=True)
            result=(path/'Caddyfile.active').read_text()
            self.assertIn('new.example.net, *.new.example.net', result)
            self.assertIn('old.example.org, *.old.example.org', result)
            self.assertIn('host old.example.org www.old.example.org', result)
            self.assertIn('header_regexp legacy_org Host', result)
            self.assertIn('https://new.example.net{uri} permanent', result)
            self.assertIn('https://{re.legacy_org.1}.new.example.net{uri} permanent', result)

    def test_legacy_domain_cannot_overlap_current_domain(self):
        for legacy in ('new.example.net', 'www.new.example.net'):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)
                shutil.copy(ROOT/'Caddyfile', path/'Caddyfile')
                (path/'.deployment-environment').write_text('production\n')
                (path/'.env').write_text(
                    'LAUNCHLMS_DOMAIN=new.example.net\n'
                    f'LAUNCHLMS_LEGACY_DOMAIN={legacy}\n'
                )
                result=subprocess.run(
                    [sys.executable,str(ROOT/'scripts/render-caddy.py')],
                    cwd=path,capture_output=True
                )
                self.assertNotEqual(0,result.returncode)

    def test_sanitizer_retains_passwords_and_rewrites_only_own_urls(self):
        from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, JSON, Boolean, select
        sanitizer=load('sanitize-copy')
        engine=create_engine('sqlite://')
        meta=MetaData()
        users=Table('user',meta,Column('id',Integer,primary_key=True),Column('password',String),Column('email',String))
        org=Table('organization',meta,Column('id',Integer,primary_key=True),Column('scripts',JSON),Column('links',JSON))
        sso=Table('ssoconnection',meta,Column('id',Integer,primary_key=True),Column('enabled',Boolean),Column('provider_config',JSON),Column('domains',JSON))
        tokens=Table('apitoken',meta,Column('id',Integer,primary_key=True))
        meta.create_all(engine)
        with engine.begin() as conn:
            conn.execute(users.insert().values(id=1,password='hash',email='person@prod.example.org'))
            conn.execute(org.insert().values(id=1,scripts={'tracking':'secret'},links={'own':'https://org.prod.example.org/file','external':'https://prod.example.org.attacker.test/file','text':'contact person@prod.example.org'}))
            conn.execute(sso.insert().values(id=1,enabled=True,provider_config={'client_secret':'secret'},domains=['prod.example.org']))
            conn.execute(tokens.insert().values(id=1))
            sanitizer.sanitize(conn,'prod.example.org','test.example.net')
            self.assertEqual('hash',conn.execute(select(users.c.password)).scalar())
            self.assertEqual('person@prod.example.org',conn.execute(select(users.c.email)).scalar())
            result=conn.execute(select(org)).mappings().one()
            self.assertEqual({},result['scripts'])
            self.assertEqual('https://org.test.example.net/file',result['links']['own'])
            self.assertEqual('https://prod.example.org.attacker.test/file',result['links']['external'])
            self.assertEqual({},conn.execute(select(sso.c.provider_config)).scalar())
            self.assertFalse(conn.execute(select(sso.c.enabled)).scalar())
            self.assertEqual([],list(conn.execute(select(tokens))))

    def test_refresh_default_org_follows_restored_database(self):
        updater=load('set-refresh-default-org')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'refresh.env'
            path.write_text(
                "LAUNCHLMS_DOMAIN='test.example.net'\n"
                "NEXT_PUBLIC_LAUNCHLMS_DEFAULT_ORG='temporary-org'\n"
            )
            updater.set_default_org(path,'copied-owner')
            result=path.read_text()
            self.assertIn("NEXT_PUBLIC_LAUNCHLMS_DEFAULT_ORG='copied-owner'",result)
            self.assertNotIn('temporary-org',result)
            with self.assertRaises(ValueError):
                updater.set_default_org(path,'invalid.example.net')


if __name__=='__main__':
    unittest.main()
