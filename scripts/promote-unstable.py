"""Three-way promotion of work authored on unstable into production.

Unstable starts as a sanitized copy of a production snapshot (the *base*).
After that, production keeps serving real users while people author new
content on unstable. This script merges the unstable side into production:

* Rows are matched across databases by a portable identity (``*_uuid``
  columns or natural keys), never by integer ids, which diverge after the
  refresh. Integer foreign keys, and known integer ids embedded in JSON, are
  translated through those identities.
* For each matched row the base decides who changed what: a column changed
  only on unstable is taken, a column changed only on production is kept, a
  column changed differently on both sides is a conflict that keeps the
  production value unless ``--prefer-unstable TABLE`` says otherwise.
* Rows that exist only on unstable are inserted. Production-only rows are
  never touched. Rows deleted on unstable are reported, and removed only with
  ``--apply-deletes`` when production left them unchanged.
* Security, payment, session, demo and derived tables are never copied, and
  user accounts are only matched unless ``--allow-new-users`` is given.

Everything runs in one transaction. Without ``--apply`` it is rolled back, so
a dry run exercises every constraint without changing production.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import MetaData, create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import sort_tables_and_constraints

# Never copied: credentials, payments, sessions, demo sandboxes, logs and
# derived indexes (resource search is rebuilt by the deploy backfill).
EXCLUDED_TABLES = frozenset("""
alembic_version apitoken auditlog usageevent customdomain ssoconnection
paymentsconfig paymentsenrollment paymentsgroup paymentsgroupresource
paymentsgroupsync paymentsoffer paymentsofferresource orgpack plan_request
organizationinvitation organizationjoinlink guestsession learningactivitypreview
oauthauthorizationcode oauthclient oauthgrant hubadvisorproviderconfiguration
democheckpoint democonfiguration demomember demosession demousage
resourcesearchdocument inboxmessage
""".split())
# What testers did while trying things out. Opt in with --include-learner-activity.
ACTIVITY_TABLES = frozenset("""
learningrun learningactivityrun learningpageprogress learningresponseattempt
learningbadgeaward objectiveprogress planobjectiveprogress requirementattainmentsource
planactivity hubconversation hubconversationmessage hubconversationmessagememory
hubconversationmessageresource hubconversationresource hubmemory hubmemorypreference
hubmemorysource hubeditrun hubeditrunevent hubeditobjectstate
""".split())
# Identity for tables without a usable unique *_uuid column.
IDENTITY_OVERRIDES = {
    "user": ("user_uuid",),
    "organizationconfig": ("org_id",),
    "userorganization": ("user_id", "org_id"),
    "usergroupuser": ("usergroup_id", "user_id"),
    "boardmember": ("board_id", "user_id"),
    "learningpageprogress": ("run_id", "page_id"),
    "resourcechannelresource": ("channel_id", "resource_id"),
    "resourcetaglink": ("resource_id", "tag_id"),
    "planattachment": ("plan_id", "asset_id"),
    "usersavedresource": ("user_id", "resource_id"),
    "usersavedresourcechannel": ("saved_resource_id", "user_channel_id"),
    "hubmemorypreference": ("org_id", "user_id"),
    "resourceauthor": ("resource_uuid", "user_id"),
    "usergroupresource": ("usergroup_id", "resource_uuid"),
    "programobjective": ("program_id", "objective_id"),
    "learningactivityrun": ("run_id", "activity_id"),
    "hubadvisorconfiguration": ("provider",),
}
# *_uuid columns that point at another row rather than naming this one.
REFERENCE_UUIDS = frozenset("""
resource_uuid memory_uuid message_uuid node_uuid media_asset_uuid source_program_uuid
source_framework_uuid superseded_by_uuid invite_code_uuid batch_uuid target_uuid
object_uuid parent_node_uuid
""".split())
# Columns that are compared but never written to an existing production row.
FROZEN_COLUMNS = {"organization": {"scripts"}}
TIMESTAMP_COLUMNS = frozenset({"update_date", "updated_at"})
# Integer ids stored inside JSON. Keys map to the referenced table; "id" is
# resolved by the first identifying sibling key; "[]" means a list of ids.
SNAPSHOT_KEYS = {
    "badge_id": "learningbadge", "org_id": "organization", "objective_id": "objective",
    "phase_id": "programphase", "user_id": "user", "staff_user_id": "user",
    "verified_by_user_id": "user", "framework_id": "requirementframework",
}
ID_SIBLINGS = (("objective_uuid", "objective"), ("phase_uuid", "programphase"),
               ("program_uuid", "program"), ("badge_uuid", "learningbadge"))
JSON_REFERENCES = {
    ("programassignment", "objective_snapshot"): SNAPSHOT_KEYS,
    ("program", "library_snapshot"): SNAPSHOT_KEYS,
    ("programassignment", "staff_user_ids"): {"[]": "user"},
    ("badgeissuerlearnerlink", "staff_user_ids"): {"[]": "user"},
    ("programassignment", "collaborators"): {"user_id": "user"},
    ("objectiveprogress", "feedback_history"): {"staff_user_id": "user"},
    ("planobjectiveprogress", "feedback_history"): {"staff_user_id": "user"},
}
ID_KEY = re.compile(r"(^id$|_ids?$)")
SENTINEL = object()


class Ref(tuple):
    """A portable pointer to a row: (table, identity)."""


def unmapped(table, value):
    return Ref(("?", table, value))


def reverse_urls(value, sources, target):
    """Point unstable URL authorities (and their org subdomains) back at production."""
    if isinstance(value, dict):
        return {key: reverse_urls(item, sources, target) for key, item in value.items()}
    if isinstance(value, list):
        return [reverse_urls(item, sources, target) for item in value]
    if not isinstance(value, str) or not sources:
        return value

    def replace(match):
        url = urlsplit(match.group())
        host = url.hostname or ""
        for source in sources:
            if host == source or host.endswith("." + source):
                netloc = host[: -len(source)] + target + (f":{url.port}" if url.port else "")
                return urlunsplit((url.scheme, netloc, url.path, url.query, url.fragment))
        return match.group()

    return re.sub(r'(?:https?|wss?)://[^\s<>"\x27]+', replace, value)


class Schema:
    def __init__(self, metadata, include_activity):
        self.metadata = metadata
        skip = EXCLUDED_TABLES | (frozenset() if include_activity else ACTIVITY_TABLES)
        self.tables = {name: table for name, table in metadata.tables.items() if name not in skip}
        self.skipped = sorted(set(metadata.tables) - set(self.tables))
        self.deferred = defaultdict(set)  # table -> FK columns written after all inserts
        tables = list(metadata.tables.values())
        # Ids inside JSON are references too: their targets must be written first.
        extra = []
        for (name, _), keys in JSON_REFERENCES.items():
            targets = set(keys.values()) | ({t for _, t in ID_SIBLINGS} if "id" not in keys and keys is SNAPSHOT_KEYS else set())
            extra += [(metadata.tables[t], metadata.tables[name]) for t in targets
                      if t != name and t in metadata.tables and name in metadata.tables]
        # Break each cycle on a nullable reference: insert NULL, then set it last.
        deferred = set()
        while True:
            result = list(sort_tables_and_constraints(
                tables, filter_fn=lambda c: True if c in deferred else None, extra_dependencies=extra))
            cyclic = [c for t, constraints in result if t is None for c in constraints or () if c not in deferred]
            if not cyclic:
                break
            candidates = sorted((c for c in cyclic if all(column.nullable for column in c.columns)),
                                key=lambda c: (c.table.name, c.name or ""))
            if not candidates:
                raise SystemExit(f"Unbreakable foreign key cycle: {sorted(c.table.name for c in cyclic)}")
            deferred.add(candidates[0])
        ordered = [table.name for table, _ in result if table is not None]
        for constraint in deferred:
            self.deferred[constraint.table.name].update(column.name for column in constraint.columns)
        self.order = [name for name in ordered if name in self.tables]
        self.fks = {}
        for name, table in self.tables.items():
            self.fks[name] = {}
            for fk in table.foreign_keys:
                target = fk.column.table.name
                if fk.column.name != "id":
                    raise SystemExit(f"{name}.{fk.parent.name} references {target}.{fk.column.name}; only id references are supported")
                self.fks[name][fk.parent.name] = target
                if target == name:
                    self.deferred[name].add(fk.parent.name)
        self.identity = {name: self._identity(table) for name, table in self.tables.items()}

    def _identity(self, table):
        if table.name in IDENTITY_OVERRIDES:
            return IDENTITY_OVERRIDES[table.name]
        uniques = [tuple(c.name for c in constraint.columns) for constraint in table.constraints
                   if constraint.__class__.__name__ == "UniqueConstraint"]
        uniques += [tuple(c.name for c in index.columns) for index in table.indexes if index.unique]
        own = lambda name: name.endswith("_uuid") and name not in REFERENCE_UUIDS
        for columns in uniques:
            if len(columns) == 1 and own(columns[0]):
                return columns
        for column in table.c:
            if own(column.name):
                return (column.name,)
        if uniques:
            return min(uniques, key=len)
        raise SystemExit(f"No portable identity for table {table.name}; add an override or exclude it")

    def pk(self, name):
        columns = [c.name for c in self.tables[name].primary_key.columns]
        if columns != ["id"]:
            raise SystemExit(f"{name} must have an integer id primary key to be promoted")
        return "id"


class Side:
    """All promotable rows of one database, keyed by portable identity."""

    def __init__(self, label, conn, schema, url_sources=(), url_target=""):
        self.label, self.conn, self.schema = label, conn, schema
        self.url_sources, self.url_target = url_sources, url_target
        self.raw = {}        # table -> id -> row
        self.key_of = {}     # table -> id -> identity
        self.by_key = {}     # table -> identity -> id
        self.emails = {}     # lowercase email -> user identity
        for name in schema.order:
            schema.pk(name)
            rows = conn.execute(select(schema.tables[name])).mappings().all()
            self.raw[name] = {row["id"]: dict(row) for row in rows}
        # Identities may reference other tables; resolve lazily with memoization.
        for name in schema.order:
            self.key_of[name], self.by_key[name] = {}, {}
        for name in schema.order:
            for row_id in self.raw[name]:
                key = self.identity(name, row_id)
                if key in self.by_key[name]:
                    raise SystemExit(f"{label}: duplicate identity {key} in {name}")
                self.by_key[name][key] = row_id
        for row_id, row in self.raw.get("user", {}).items():
            if row.get("email"):
                self.emails[row["email"].strip().lower()] = self.key_of["user"][row_id]

    def identity(self, name, row_id):
        cache = self.key_of[name]
        if row_id not in cache:
            row = self.raw[name][row_id]
            parts = []
            for column in self.schema.identity[name]:
                value = row[column]
                if column in self.schema.fks[name]:
                    value = self.ref(self.schema.fks[name][column], value)
                parts.append(value)
            cache[row_id] = Ref((name, tuple(parts)))
        return cache[row_id]

    def ref(self, table, value):
        if value is None:
            return None
        if table in self.raw and value in self.raw[table]:
            return self.identity(table, value)
        return unmapped(table, value)

    def portable(self, name, row_id):
        """The row with every integer reference replaced by a portable identity."""
        row, result = self.raw[name][row_id], {}
        for column, value in row.items():
            if column == "id":
                continue
            if column in self.schema.fks[name]:
                value = self.ref(self.schema.fks[name][column], value)
            elif (name, column) in JSON_REFERENCES:
                value = self.json_refs(value, JSON_REFERENCES[(name, column)])
            if self.url_sources:
                value = reverse_urls(value, self.url_sources, self.url_target)
            result[column] = value
        return result

    def json_refs(self, value, keys):
        if isinstance(value, list):
            if "[]" in keys:
                return [self.ref(keys["[]"], item) if isinstance(item, int) else item for item in value]
            return [self.json_refs(item, keys) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "id" and isinstance(item, int):
                table = next((t for sibling, t in ID_SIBLINGS if sibling in value), None)
                result[key] = self.ref(table, item) if table else item
            elif key in keys and isinstance(item, int):
                result[key] = self.ref(keys[key], item)
            else:
                result[key] = self.json_refs(item, keys)
        return result


def unresolved_json_ids(value, path=""):
    """Integer ids left in JSON after translation: they would point at the wrong rows."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, int) and not isinstance(item, bool) and ID_KEY.search(key):
                found.append(f"{path}.{key}")
            elif isinstance(item, list) and ID_KEY.search(key) and any(isinstance(i, int) and not isinstance(i, bool) for i in item):
                found.append(f"{path}.{key}[]")
            else:
                found += unresolved_json_ids(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += unresolved_json_ids(item, f"{path}[{index}]")
    return found


class Promotion:
    def __init__(self, schema, base, prod, unstable, conn, options):
        self.schema, self.base, self.prod, self.unstable = schema, base, prod, unstable
        self.conn, self.options = conn, options
        self.report = {"tables": {}, "new_users": [], "warnings": []}
        self.pending_deferred = []  # (table, prod id, {column: portable value})
        self.user_alias = {}  # unstable user identity -> production user identity

    def table_report(self, name):
        return self.report["tables"].setdefault(name, {
            "inserted": 0, "updated": 0, "unchanged": 0, "conflicts": [], "skipped": [],
            "deleted_on_production": 0, "deleted_on_unstable": [], "deleted": 0,
        })

    # Translating portable values back to production integers.
    def resolve(self, value):
        if isinstance(value, Ref):
            if value[0] == "?":
                raise LookupError(f"{value[1]} row {value[2]} is not promotable")
            key = self.translate_key(value)
            row_id = self.prod.by_key.get(key[0], {}).get(key) if key else None
            if row_id is None:
                raise LookupError(f"{value[0]} {value[1]!r} is not in production")
            return row_id
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        return value

    def concrete(self, name, values):
        """Production column values; unresolvable optional references become NULL."""
        table, result, notes = self.schema.tables[name], {}, []
        for column, value in values.items():
            try:
                result[column] = self.resolve(value)
            except LookupError as error:
                if column in self.schema.fks[name] and table.c[column].nullable:
                    result[column] = None
                    notes.append(f"{column} set to NULL: {error}")
                else:
                    raise LookupError(f"{column}: {error}") from None
        return result, notes

    def run(self):
        self.match_users()
        for name in self.schema.order:
            if name == "user" and not self.options.allow_new_users:
                continue
            self.merge_table(name)
        self.apply_deferred()
        self.deletions()
        return self.report

    def match_users(self):
        """Pair unstable accounts with production ones by uuid, then email."""
        if "user" not in self.schema.tables:
            return
        report = self.table_report("user")
        for key, row_id in self.unstable.by_key["user"].items():
            if key in self.prod.by_key["user"]:
                report["unchanged"] += 1
                continue
            email = (self.unstable.raw["user"][row_id].get("email") or "").strip().lower()
            if email in self.prod.emails:
                self.user_alias[key] = self.prod.emails[email]
                report["unchanged"] += 1
            elif self.options.allow_new_users:
                pass  # Inserted by merge_table like any other row.
            else:
                self.report["new_users"].append(email or str(key[1]))

    def merge_table(self, name):
        report = self.table_report(name)
        deferred = self.schema.deferred[name]
        prefer_unstable = name in self.options.prefer_unstable
        for key, u_id in self.unstable.by_key[name].items():
            if name == "user" and (key in self.prod.by_key["user"] or key in self.user_alias):
                continue
            target_key = self.user_alias.get(key, key) if name == "user" else self.translate_key(key)
            if target_key is None:
                report["skipped"].append({"key": repr(key[1]), "reason": "identity references a row that is not in production"})
                continue
            u_row = self.align(self.unstable.portable(name, u_id))
            p_id = self.prod.by_key[name].get(target_key)
            b_id = self.base.by_key[name].get(key)
            if p_id is None:
                if b_id is not None:
                    report["deleted_on_production"] += 1
                    continue
                self.insert(name, key, u_row, deferred, report)
                continue
            p_row = self.prod.portable(name, p_id)
            b_row = self.base.portable(name, b_id) if b_id is not None else None
            changes, conflicts = {}, []
            for column, u_value in u_row.items():
                p_value = p_row.get(column)
                if u_value == p_value or column in TIMESTAMP_COLUMNS:
                    continue
                b_value = b_row.get(column, SENTINEL) if b_row is not None else SENTINEL
                if b_value is not SENTINEL and p_value == b_value:
                    changes[column] = u_value
                elif b_value is not SENTINEL and u_value == b_value:
                    continue
                else:
                    conflicts.append(column)
                    if prefer_unstable:
                        changes[column] = u_value
            frozen = FROZEN_COLUMNS.get(name, set())
            changes = {c: v for c, v in changes.items() if c not in frozen}
            if conflicts:
                report["conflicts"].append({
                    "key": repr(key[1]),
                    "columns": {c: {"production": short(p_row.get(c)), "unstable": short(u_row.get(c)),
                                    "base": short(b_row.get(c)) if b_row else None} for c in conflicts},
                    "kept": "unstable" if prefer_unstable else "production",
                })
            if not changes:
                report["unchanged"] += 1
                continue
            for column in TIMESTAMP_COLUMNS & set(u_row):
                if u_row[column] is not None and (p_row.get(column) is None or str(u_row[column]) > str(p_row[column])):
                    changes[column] = u_row[column]
            self.update(name, key, p_id, changes, deferred, report)

    def align(self, value):
        """Rewrite unstable identities inside a row to their production equivalents."""
        if isinstance(value, Ref):
            return self.translate_key(value) or value
        if isinstance(value, dict):
            return {key: self.align(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.align(item) for item in value]
        return value

    def translate_key(self, key):
        """The production identity of an unstable identity (user aliases applied)."""
        def walk(value):
            if isinstance(value, Ref):
                if value[0] == "?":
                    raise LookupError
                if value[0] == "user":
                    return self.user_alias.get(value, value)
                return Ref((value[0], tuple(walk(part) for part in value[1])))
            return value
        try:
            return walk(key)
        except LookupError:
            return None

    def check_json(self, name, values, report, key):
        flagged = [f"{column}{path}" for column, value in values.items()
                   if isinstance(value, (dict, list)) for path in unresolved_json_ids(value)]
        if flagged and not self.options.accept_unmapped_json:
            report["skipped"].append({"key": repr(key[1]), "reason": "unmapped integer ids in JSON: " + ", ".join(flagged[:5])})
            return False
        return True

    def insert(self, name, key, u_row, deferred, report):
        values = {c: v for c, v in u_row.items() if c not in deferred}
        if not self.check_json(name, values, report, key):
            return
        try:
            concrete, notes = self.concrete(name, values)
        except LookupError as error:
            report["skipped"].append({"key": repr(key[1]), "reason": str(error)})
            return
        table = self.schema.tables[name]
        try:
            with self.conn.begin_nested():
                new_id = self.conn.execute(table.insert().values(**concrete).returning(table.c.id)).scalar_one()
        except IntegrityError as error:
            report["skipped"].append({"key": repr(key[1]), "reason": "constraint: " + first_line(error)})
            return
        self.prod.by_key[name][self.translate_key(key) or key] = new_id
        report["inserted"] += 1
        for note in notes:
            self.report["warnings"].append(f"{name} {key[1]!r}: {note}")
        later = {c: u_row[c] for c in deferred if u_row.get(c) is not None}
        if later:
            self.pending_deferred.append((name, key, new_id, later))

    def update(self, name, key, p_id, changes, deferred, report):
        now = {c: v for c, v in changes.items() if c not in deferred}
        later = {c: v for c, v in changes.items() if c in deferred}
        if now:
            if not self.check_json(name, now, report, key):
                return
            try:
                concrete, notes = self.concrete(name, now)
            except LookupError as error:
                report["skipped"].append({"key": repr(key[1]), "reason": str(error)})
                return
            table = self.schema.tables[name]
            try:
                with self.conn.begin_nested():
                    self.conn.execute(table.update().where(table.c.id == p_id).values(**concrete))
            except IntegrityError as error:
                report["skipped"].append({"key": repr(key[1]), "reason": "constraint: " + first_line(error)})
                return
            for note in notes:
                self.report["warnings"].append(f"{name} {key[1]!r}: {note}")
        if later:
            self.pending_deferred.append((name, key, p_id, later))
        report["updated"] += 1

    def apply_deferred(self):
        for name, key, row_id, values in self.pending_deferred:
            table = self.schema.tables[name]
            try:
                concrete, notes = self.concrete(name, values)
                with self.conn.begin_nested():
                    self.conn.execute(table.update().where(table.c.id == row_id).values(**concrete))
            except (LookupError, IntegrityError) as error:
                self.report["warnings"].append(f"{name} {key[1]!r}: deferred reference not set: {first_line(error)}")
                continue
            for note in notes:
                self.report["warnings"].append(f"{name} {key[1]!r}: {note}")

    def deletions(self):
        """Rows removed on unstable that production left as they were in the base."""
        candidates = []
        for name in self.schema.order:
            if name == "user":
                continue
            report = self.table_report(name)
            unstable_keys = {self.translate_key(k) for k in self.unstable.by_key[name]}
            for key, b_id in self.base.by_key[name].items():
                if key in self.unstable.by_key[name] or key in unstable_keys:
                    continue
                p_id = self.prod.by_key[name].get(key)
                if p_id is None:
                    continue
                if self.prod.portable(name, p_id) != self.base.portable(name, b_id):
                    report["conflicts"].append({"key": repr(key[1]), "columns": {}, "kept": "production",
                                                "note": "deleted on unstable but changed on production"})
                    continue
                report["deleted_on_unstable"].append(repr(key[1]))
                candidates.append((name, p_id))
        if not self.options.apply_deletes:
            return
        position = {name: index for index, name in enumerate(self.schema.order)}
        for name, p_id in sorted(candidates, key=lambda item: -position[item[0]]):
            table = self.schema.tables[name]
            try:
                with self.conn.begin_nested():
                    self.conn.execute(table.delete().where(table.c.id == p_id))
                self.table_report(name)["deleted"] += 1
            except IntegrityError as error:
                self.report["warnings"].append(f"{name} id {p_id}: not deleted: {first_line(error)}")


def transactional(engine):
    """pysqlite skips BEGIN before SAVEPOINT; emit it so a dry run really rolls back."""
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def connect(dbapi_connection, _):
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def begin(conn):
            conn.exec_driver_sql("BEGIN")
    return engine


def short(value, limit=160):
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def first_line(error):
    return str(getattr(error, "orig", error)).strip().splitlines()[0]


def check_schemas(connections):
    """All three databases must be at the same migration head with the same tables."""
    heads, shapes = {}, {}
    for label, conn in connections.items():
        heads[label] = sorted(r[0] for r in conn.exec_driver_sql("SELECT version_num FROM alembic_version"))
        metadata = MetaData()
        metadata.reflect(conn)
        shapes[label] = {name: sorted(c.name for c in table.c) for name, table in metadata.tables.items()}
    if len({json.dumps(v) for v in heads.values()}) != 1:
        raise SystemExit(f"Alembic heads differ: {heads}. Migrate the base and deploy the same release to unstable first.")
    if len({json.dumps(v, sort_keys=True) for v in shapes.values()}) != 1:
        raise SystemExit("Table/column sets differ between base, production and unstable")


def copy_content(source, target, apply):
    """Copy uploaded files that production lacks; never overwrite production files."""
    result = {"copied": 0, "identical": 0, "conflicts": []}
    source, target = Path(source), Path(target)
    def digest(path):
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"Refusing symlink in unstable content: {path}")
        if not path.is_file():
            continue
        destination = target / path.relative_to(source)
        if destination.exists():
            if digest(destination) == digest(path):
                result["identical"] += 1
            else:
                result["conflicts"].append(str(path.relative_to(source)))
            continue
        result["copied"] += 1
        if apply:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    return result


def summarize(report):
    lines = []
    for name, entry in sorted(report["tables"].items()):
        busy = entry["inserted"] or entry["updated"] or entry["conflicts"] or entry["skipped"] or entry["deleted_on_unstable"] or entry["deleted_on_production"]
        if not busy:
            continue
        lines.append(f"{name:34} +{entry['inserted']:<5} ~{entry['updated']:<5} conflicts {len(entry['conflicts']):<4} "
                     f"skipped {len(entry['skipped']):<4} removed-on-unstable {len(entry['deleted_on_unstable']):<4} "
                     f"removed-on-prod {entry['deleted_on_production']}")
    if report["new_users"]:
        # Counts only: this summary reaches public Actions logs. Emails stay in the report.
        lines.append(f"{len(report['new_users'])} unstable-only accounts not copied (use --allow-new-users)")
    if report["warnings"]:
        lines.append(f"{len(report['warnings'])} warnings (see report)")
    if "content" in report:
        content = report["content"]
        lines.append(f"files: {content['copied']} new, {content['identical']} identical, {len(content['conflicts'])} differ (production kept)")
    lines.append(f"excluded tables: {', '.join(report['excluded_tables'])}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="SQLAlchemy URL of the migrated refresh-base snapshot")
    parser.add_argument("--unstable", required=True, help="SQLAlchemy URL of the restored unstable copy")
    parser.add_argument("--production", default=os.environ.get("LAUNCHLMS_SQL_CONNECTION_STRING"))
    parser.add_argument("--unstable-domain", action="append", default=[], help="Unstable host(s) to rewrite back to production URLs")
    parser.add_argument("--production-domain", default="")
    parser.add_argument("--report", required=True)
    parser.add_argument("--content-source")
    parser.add_argument("--content-target")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-deletes", action="store_true")
    parser.add_argument("--allow-new-users", action="store_true")
    parser.add_argument("--include-learner-activity", action="store_true")
    parser.add_argument("--accept-unmapped-json", action="store_true")
    parser.add_argument("--prefer-unstable", action="append", default=[], metavar="TABLE")
    options = parser.parse_args(argv)
    if options.unstable_domain and not options.production_domain:
        parser.error("--production-domain is required with --unstable-domain")

    engines = {label: transactional(create_engine(url)) for label, url in
               (("base", options.base), ("production", options.production), ("unstable", options.unstable))}
    if len({str(engine.url) for engine in engines.values()}) != 3:
        raise SystemExit("Base, production and unstable must be three different databases")
    with engines["base"].connect() as base_conn, engines["unstable"].connect() as unstable_conn, \
            engines["production"].connect() as conn:
        check_schemas({"base": base_conn, "production": conn, "unstable": unstable_conn})
        metadata = MetaData()
        metadata.reflect(conn)
        schema = Schema(metadata, options.include_learner_activity)
        unknown = set(options.prefer_unstable) - set(schema.tables)
        if unknown:
            raise SystemExit(f"--prefer-unstable names unknown or excluded tables: {sorted(unknown)}")
        conn.rollback()  # End the reflection transaction; everything below is one unit.
        transaction = conn.begin()
        base = Side("base", base_conn, schema)
        production = Side("production", conn, schema)
        domains = sorted(options.unstable_domain, key=len, reverse=True)
        unstable = Side("unstable", unstable_conn, schema, domains, options.production_domain)
        promotion = Promotion(schema, base, production, unstable, conn, options)
        report = promotion.run()
        report["excluded_tables"] = schema.skipped
        if options.content_source:
            report["content"] = copy_content(options.content_source, options.content_target, options.apply)
        report["applied"] = bool(options.apply)
        if options.apply:
            transaction.commit()
        else:
            transaction.rollback()
    Path(options.report).write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(summarize(report))
    print(("Applied." if options.apply else "Dry run: production unchanged.") + f" Full report: {options.report}")


if __name__ == "__main__":
    sys.exit(main())
