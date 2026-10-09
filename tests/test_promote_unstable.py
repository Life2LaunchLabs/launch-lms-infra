import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

try:
    from sqlalchemy import (JSON, Column, ForeignKey, Integer, MetaData, String, Table,
                            UniqueConstraint, create_engine, select)
except ImportError:  # pragma: no cover - matches the sanitizer test's optional dependency
    create_engine = None


def load():
    spec = importlib.util.spec_from_file_location('promote_unstable', ROOT/'scripts/promote-unstable.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def schema():
    metadata = MetaData()
    Table('alembic_version', metadata, Column('version_num', String, primary_key=True))
    Table('organization', metadata, Column('id', Integer, primary_key=True),
          Column('org_uuid', String, unique=True), Column('slug', String, unique=True),
          Column('about', String), Column('scripts', JSON), Column('update_date', String))
    Table('user', metadata, Column('id', Integer, primary_key=True), Column('user_uuid', String),
          Column('email', String), Column('password', String))
    Table('role', metadata, Column('id', Integer, primary_key=True), Column('role_uuid', String))
    Table('userorganization', metadata, Column('id', Integer, primary_key=True),
          Column('user_id', ForeignKey('user.id'), nullable=False),
          Column('org_id', ForeignKey('organization.id'), nullable=False),
          Column('role_id', ForeignKey('role.id'), nullable=False))
    Table('learningbadge', metadata, Column('id', Integer, primary_key=True),
          Column('badge_uuid', String, unique=True), Column('org_id', ForeignKey('organization.id')),
          Column('name', String), Column('description', String), Column('update_date', String),
          Column('active_version_id', ForeignKey('learningbadgeversion.id'), nullable=True))
    Table('learningbadgeversion', metadata, Column('id', Integer, primary_key=True),
          Column('version_uuid', String, unique=True),
          Column('badge_id', ForeignKey('learningbadge.id'), nullable=False),
          Column('based_on_version_id', ForeignKey('learningbadgeversion.id'), nullable=True),
          Column('created_by_user_id', ForeignKey('user.id'), nullable=True))
    Table('learningpage', metadata, Column('id', Integer, primary_key=True),
          Column('page_uuid', String, unique=True),
          Column('version_id', ForeignKey('learningbadgeversion.id'), nullable=False),
          Column('content', JSON))
    Table('objective', metadata, Column('id', Integer, primary_key=True),
          Column('objective_uuid', String, unique=True), Column('badge_id', ForeignKey('learningbadge.id')))
    Table('programassignment', metadata, Column('id', Integer, primary_key=True),
          Column('assignment_uuid', String, unique=True),
          Column('objective_snapshot', JSON), Column('staff_user_ids', JSON))
    Table('program', metadata, Column('id', Integer, primary_key=True),
          Column('program_uuid', String, unique=True), Column('library_snapshot', JSON))
    Table('programphase', metadata, Column('id', Integer, primary_key=True),
          Column('phase_uuid', String, unique=True), Column('program_id', ForeignKey('program.id'), nullable=False))
    Table('apitoken', metadata, Column('id', Integer, primary_key=True), Column('token_hash', String))
    Table('learningrun', metadata, Column('id', Integer, primary_key=True), Column('run_uuid', String, unique=True))
    return metadata


@unittest.skipIf(create_engine is None, 'SQLAlchemy is required')
class PromoteUnstableTests(unittest.TestCase):
    def setUp(self):
        self.module = load()
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        self.metadata = schema()
        self.urls = {}
        base = self.engine('base')
        self.metadata.create_all(base)
        t = self.metadata.tables
        with base.begin() as c:
            c.execute(t['alembic_version'].insert(), [{'version_num': 'head'}])
            c.execute(t['organization'].insert(), [{'id': 1, 'org_uuid': 'org_a', 'slug': 'a', 'about': 'https://a.prod.test/x', 'scripts': {'s': 1}, 'update_date': '1'}])
            c.execute(t['user'].insert(), [{'id': 1, 'user_uuid': 'alice', 'email': 'alice@x', 'password': 'h'}])
            c.execute(t['role'].insert(), [{'id': 1, 'role_uuid': 'member'}])
            c.execute(t['userorganization'].insert(), [{'id': 1, 'user_id': 1, 'org_id': 1, 'role_id': 1}])
            c.execute(t['learningbadge'].insert(), [
                {'id': 1, 'badge_uuid': 'x', 'org_id': 1, 'name': 'X', 'description': 'd', 'update_date': '1'},
                {'id': 2, 'badge_uuid': 'y', 'org_id': 1, 'name': 'Y', 'description': 'd', 'update_date': '1'}])
            c.execute(t['learningbadgeversion'].insert(), [{'id': 1, 'version_uuid': 'x1', 'badge_id': 1}, {'id': 2, 'version_uuid': 'y1', 'badge_id': 2}])
            c.execute(t['learningbadge'].update().where(t['learningbadge'].c.id == 1).values(active_version_id=1))
            c.execute(t['learningpage'].insert(), [{'id': 1, 'page_uuid': 'px', 'version_id': 1, 'content': {}},
                                                   {'id': 2, 'page_uuid': 'py', 'version_id': 2, 'content': {}}])
        for label in ('production', 'unstable'):
            shutil.copy(self.directory/'base.db', self.directory/f'{label}.db')
            self.engine(label)
        with self.urls['production'].begin() as c:
            c.execute(t['user'].insert(), [{'id': 2, 'user_uuid': 'carol-prod', 'email': 'Carol@x', 'password': 'h'}])
            c.execute(t['learningbadge'].insert(), [{'id': 3, 'badge_uuid': 'z', 'org_id': 1, 'name': 'Z', 'update_date': '2'}])
            c.execute(t['learningbadge'].update().where(t['learningbadge'].c.id == 2).values(name='Y prod', update_date='2'))
            c.execute(t['learningbadge'].update().where(t['learningbadge'].c.id == 1).values(description='prod', update_date='2'))
        with self.urls['unstable'].begin() as c:
            c.execute(t['user'].insert(), [{'id': 2, 'user_uuid': 'dave', 'email': 'dave@test', 'password': 't'},
                                           {'id': 3, 'user_uuid': 'carol-unstable', 'email': 'carol@x', 'password': 't'}])
            c.execute(t['userorganization'].insert(), [{'id': 2, 'user_id': 2, 'org_id': 1, 'role_id': 1},
                                                       {'id': 3, 'user_id': 3, 'org_id': 1, 'role_id': 1}])
            c.execute(t['learningbadge'].insert(), [{'id': 3, 'badge_uuid': 'w', 'org_id': 1, 'name': 'W', 'update_date': '3'}])
            c.execute(t['learningbadgeversion'].insert(), [
                {'id': 3, 'version_uuid': 'w1', 'badge_id': 3, 'based_on_version_id': None, 'created_by_user_id': 2},
                {'id': 4, 'version_uuid': 'x2', 'badge_id': 1, 'based_on_version_id': 1, 'created_by_user_id': 3}])
            c.execute(t['learningbadge'].update().where(t['learningbadge'].c.id == 3).values(active_version_id=3))
            c.execute(t['learningbadge'].update().where(t['learningbadge'].c.id == 1).values(name='X edited', description='unstable', update_date='3'))
            c.execute(t['learningpage'].insert(), [{'id': 3, 'page_uuid': 'pw', 'version_id': 3,
                                                    'content': {'link': 'https://a.unstable.test/badges/w'}}])
            c.execute(t['learningpage'].delete().where(t['learningpage'].c.page_uuid == 'py'))
            c.execute(t['organization'].update().values(about='https://a.unstable.test/x', scripts={}))
            c.execute(t['objective'].insert(), [{'id': 1, 'objective_uuid': 'o1', 'badge_id': 3}])
            c.execute(t['programassignment'].insert(), [{'id': 1, 'assignment_uuid': 'a1',
                'objective_snapshot': [{'id': 1, 'objective_uuid': 'o1', 'badge_id': 3}], 'staff_user_ids': [1, 3]}])
            c.execute(t['apitoken'].insert(), [{'id': 1, 'token_hash': 'secret'}])
            # A program's snapshot names its own phases: a JSON reference against FK order.
            c.execute(t['program'].insert(), [{'id': 1, 'program_uuid': 'p1', 'library_snapshot': None}])
            c.execute(t['programphase'].insert(), [{'id': 1, 'phase_uuid': 'ph1', 'program_id': 1}])
            c.execute(t['program'].update().values(library_snapshot={'phases': [{'id': 1, 'phase_uuid': 'ph1'}]}))
            c.execute(t['learningrun'].insert(), [{'id': 1, 'run_uuid': 'tester-run'}])

    def engine(self, label):
        engine = create_engine(f'sqlite:///{self.directory/label}.db')
        self.urls[label] = engine
        return engine

    def promote(self, *extra):
        report = self.directory/'report.json'
        self.module.main(['--base', str(self.urls['base'].url), '--production', str(self.urls['production'].url),
                          '--unstable', str(self.urls['unstable'].url), '--unstable-domain', 'unstable.test',
                          '--production-domain', 'prod.test', '--report', str(report), *extra])
        return json.loads(report.read_text())

    def rows(self, table, *columns):
        t = self.metadata.tables[table]
        with self.urls['production'].connect() as c:
            return [tuple(r) for r in c.execute(select(*(t.c[name] for name in columns)).order_by(t.c.id))]

    def test_dry_run_reports_without_writing(self):
        before = self.rows('learningbadge', 'badge_uuid', 'name')
        report = self.promote()
        self.assertFalse(report['applied'])
        self.assertEqual(self.rows('learningbadge', 'badge_uuid', 'name'), before)
        self.assertEqual(report['tables']['learningbadge']['inserted'], 1)
        self.assertEqual(report['new_users'], ['dave@test'])
        self.assertIn('apitoken', report['excluded_tables'])
        self.assertIn('learningrun', report['excluded_tables'])

    def test_apply_merges_three_ways_by_identity(self):
        report = self.promote('--apply', '--apply-deletes')
        badges = {uuid: (name, description) for uuid, name, description in self.rows('learningbadge', 'badge_uuid', 'name', 'description')}
        # Unstable-only edit taken; edit on both sides keeps production; production-only edit kept.
        self.assertEqual(badges['x'], ('X edited', 'prod'))
        self.assertEqual(badges['y'], ('Y prod', 'd'))
        self.assertIn('z', badges)
        self.assertEqual(report['tables']['learningbadge']['conflicts'][0]['columns'].keys(), {'description'})
        ids = {uuid: row_id for row_id, uuid in self.rows('learningbadge', 'id', 'badge_uuid')}
        versions = {uuid: rest for uuid, *rest in self.rows('learningbadgeversion', 'version_uuid', 'id', 'badge_id', 'based_on_version_id', 'created_by_user_id')}
        # New rows get fresh production ids and every reference follows them.
        self.assertNotEqual(ids['w'], 3)
        self.assertEqual(versions['w1'][1], ids['w'])
        self.assertIsNone(versions['w1'][3])  # Unmatched tester account becomes NULL.
        self.assertEqual(versions['x2'][1:], [1, 1, 2])  # carol matched by email.
        active = dict(self.rows('learningbadge', 'badge_uuid', 'active_version_id'))
        self.assertEqual(active['w'], versions['w1'][0])
        pages = dict(self.rows('learningpage', 'page_uuid', 'content'))
        self.assertNotIn('py', pages)
        self.assertEqual(pages['pw'], {'link': 'https://a.prod.test/badges/w'})
        self.assertEqual(self.rows('organization', 'about', 'scripts'), [('https://a.prod.test/x', {'s': 1})])
        (snapshot, staff), = self.rows('programassignment', 'objective_snapshot', 'staff_user_ids')
        objective_id, = [row_id for row_id, in self.rows('objective', 'id')]
        self.assertEqual(snapshot, [{'id': objective_id, 'objective_uuid': 'o1', 'badge_id': ids['w']}])
        self.assertEqual(staff, [1, 2])
        memberships = self.rows('userorganization', 'user_id')
        self.assertEqual(sorted(memberships), [(1,), (2,)])
        self.assertEqual(self.rows('apitoken', 'token_hash'), [])
        self.assertEqual(len(self.rows('user', 'id')), 2)
        # Promotion is idempotent.
        again = self.promote('--apply')
        self.assertTrue(all(entry['inserted'] == 0 and entry['updated'] == 0 for entry in again['tables'].values()))

    def test_json_ids_pointing_against_foreign_key_order_are_written_last(self):
        with self.urls['production'].begin() as c:  # Shift production ids so a stale id would show.
            c.execute(self.metadata.tables['program'].insert(), [{'id': 7, 'program_uuid': 'prod-only'}])
            c.execute(self.metadata.tables['programphase'].insert(), [{'id': 7, 'phase_uuid': 'prod-ph', 'program_id': 7}])
        self.promote('--apply')
        phase_id = dict(self.rows('programphase', 'phase_uuid', 'id'))['ph1']
        snapshot = dict(self.rows('program', 'program_uuid', 'library_snapshot'))['p1']
        self.assertNotEqual(phase_id, 1)
        self.assertEqual(snapshot, {'phases': [{'id': phase_id, 'phase_uuid': 'ph1'}]})

    def test_deletions_need_explicit_opt_in(self):
        report = self.promote('--apply')
        self.assertEqual(report['tables']['learningpage']['deleted_on_unstable'], ["('py',)"])
        self.assertIn('py', dict(self.rows('learningpage', 'page_uuid', 'id')))

    def test_prefer_unstable_resolves_conflicts(self):
        self.promote('--apply', '--prefer-unstable', 'learningbadge')
        self.assertIn(('x', 'unstable'), self.rows('learningbadge', 'badge_uuid', 'description'))

    def test_refuses_mismatched_schema_heads(self):
        with self.urls['unstable'].begin() as c:
            c.execute(self.metadata.tables['alembic_version'].update().values(version_num='newer'))
        with self.assertRaises(SystemExit):
            self.promote()

    def test_content_copy_never_overwrites_production(self):
        source, target = self.directory/'source', self.directory/'target'
        (source/'orgs').mkdir(parents=True)
        (target/'orgs').mkdir(parents=True)
        (source/'orgs/new.png').write_bytes(b'new')
        (source/'orgs/same.png').write_bytes(b'same')
        (target/'orgs/same.png').write_bytes(b'same')
        (source/'orgs/edited.png').write_bytes(b'unstable')
        (target/'orgs/edited.png').write_bytes(b'production')
        result = self.module.copy_content(source, target, apply=True)
        self.assertEqual((result['copied'], result['identical'], result['conflicts']), (1, 1, ['orgs/edited.png']))
        self.assertEqual((target/'orgs/edited.png').read_bytes(), b'production')
        self.assertEqual((target/'orgs/new.png').read_bytes(), b'new')


class PromotionWorkflowTests(unittest.TestCase):
    def test_transfer_is_encrypted_short_lived_and_serialized_with_deploys(self):
        workflow = (ROOT/'.github/workflows/promote-unstable.yaml').read_text()
        transfer = (ROOT/'scripts/promotion-transfer.sh').read_text()
        self.assertIn('group: deploy-production', workflow)
        self.assertIn('group: deploy-unstable', workflow)
        self.assertIn('retention-days: 1', workflow)
        self.assertIn('StrictHostKeyChecking=yes', workflow)
        self.assertIn('git merge-base --is-ancestor $INFRA_REVISION origin/main', workflow)
        self.assertIn('openssl pkeyutl -encrypt -pubin', transfer)
        self.assertIn('[[ "$environment" == production ]]', transfer)
        self.assertNotIn('report.json"', workflow)

    def test_snapshot_checks_disk_before_pausing_the_app(self):
        snapshot = (ROOT/'scripts/snapshot.sh').read_text()
        self.assertLess(snapshot.index('Not enough free disk'), snapshot.index('docker compose stop'))
        transfer = (ROOT/'scripts/promotion-transfer.sh').read_text()
        self.assertNotIn('cp -a', transfer)
        self.assertIn('trap \'rm -rf -- "$work" "$destination"\' EXIT', transfer)

    def test_base_from_before_a_domain_move_maps_urls_to_its_own_domain(self):
        wrapper = (ROOT/'scripts/promote-unstable.sh').read_text()
        self.assertIn("base_domain in (env['LAUNCHLMS_DOMAIN'], env.get('LAUNCHLMS_LEGACY_DOMAIN'))", wrapper)
        self.assertIn("'--production-domain', base_domain", wrapper)
        module = load()
        mapped = module.reverse_urls('https://org.unstable.example.app/x https://example.app/y https://old.dev/z',
                                     ['unstable.example.app', 'old.dev'], 'legacy.com')
        self.assertEqual(mapped, 'https://org.legacy.com/x https://example.app/y https://legacy.com/z')

    def test_public_summary_has_no_emails(self):
        module = load()
        report = {'tables': {}, 'new_users': ['person@example.org'], 'warnings': [], 'excluded_tables': []}
        self.assertNotIn('person@example.org', module.summarize(report))


if __name__ == '__main__':
    unittest.main()
