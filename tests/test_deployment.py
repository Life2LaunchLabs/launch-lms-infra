import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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


if __name__=='__main__':
    unittest.main()
