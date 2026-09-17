"""Runtime deployment contract for the dedicated operations host."""

import importlib.util
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("control_plane_env", ROOT / "scripts/validate-control-plane-env.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
from environment_topology import load_topology, validate_topology

RENDER_SPEC = importlib.util.spec_from_file_location("render_runtime", ROOT / "scripts/render-control-plane-runtime.py")
RENDER = importlib.util.module_from_spec(RENDER_SPEC)
RENDER_SPEC.loader.exec_module(RENDER)


def runtime():
    password = "a" * 48
    control = {
        "OPERATIONS_DATABASE_URL": f"postgresql+psycopg://operations:{password}@postgres/operations",
        "OPERATIONS_PUBLIC_URL": "https://ops.example.org", "OPERATIONS_DOMAIN": "ops.example.org",
        "OPERATIONS_EXPECTED_IP": "143.110.225.231", "OPERATIONS_SESSION_SECRET": "b" * 64,
        "OPERATIONS_ENVIRONMENT": "production", "GITHUB_OAUTH_CLIENT_ID": "client",
        "GITHUB_OAUTH_CLIENT_SECRET": "secret", "GITHUB_APP_ID": "123",
        "GITHUB_APP_INSTALLATION_ID": "456", "GITHUB_APP_PRIVATE_KEY": "private-key",
        "GITHUB_ALLOWED_ORG": "Life2LaunchLabs", "GITHUB_ALLOWED_REPO": "Life2LaunchLabs/launch-lms",
        "JIRA_BASE_URL": "https://example.atlassian.net", "JIRA_DELIVERY_EMAIL": "delivery@example.org",
        "JIRA_DELIVERY_TOKEN": "delivery-token", "JIRA_FEEDBACK_EMAIL": "feedback@example.org",
        "JIRA_FEEDBACK_TOKEN": "feedback-token",
    }
    postgres = {"POSTGRES_USER": "operations", "POSTGRES_DB": "operations", "POSTGRES_PASSWORD": password}
    resolver = lambda *args, **kwargs: [(None, None, None, None, ("143.110.225.231", 443))]
    return control, postgres, resolver


class ControlPlaneEnvironmentTests(unittest.TestCase):
    def test_individual_secrets_render_matching_private_files(self):
        source = {secret: f"value-for-{name}" for name, secret in RENDER.SECRET_MAP.items()}
        source.update({
            "OPERATIONS_POSTGRES_PASSWORD": "a" * 40,
            "OPERATIONS_SESSION_SECRET": "b" * 48,
            "OPERATIONS_GITHUB_APP_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----",
            "OPERATIONS_JIRA_BASE_URL": "https://example.atlassian.net",
            "OPERATIONS_JIRA_DELIVERY_TOKEN": "delivery-token",
            "OPERATIONS_JIRA_FEEDBACK_TOKEN": "feedback-token",
        })
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            RENDER.render(destination, source)
            from env_file import read_env
            control = read_env(destination / "control-plane.env")
            postgres = read_env(destination / "postgres.env")
            self.assertEqual(control["OPERATIONS_DATABASE_URL"],
                             f"postgresql+psycopg://operations:{source['OPERATIONS_POSTGRES_PASSWORD']}@postgres/operations")
            self.assertEqual(postgres["POSTGRES_PASSWORD"], source["OPERATIONS_POSTGRES_PASSWORD"])
            self.assertEqual((destination / "github-app.pem").read_text().strip(),
                             source["OPERATIONS_GITHUB_APP_PRIVATE_KEY"])
            for name in ("control-plane.env", "postgres.env", "github-app.pem"):
                self.assertEqual((destination / name).stat().st_mode & 0o777, 0o600)
            self.assertNotIn("OPERATIONS_JIRA_FEEDBACK_TOKEN", (destination / "postgres.env").read_text())
        for bad in ("short", "x" * 32 + "@"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "URL-safe"):
                    RENDER.runtime({**source, "OPERATIONS_POSTGRES_PASSWORD": bad})
        self.assertEqual(RENDER.runtime({**source, "OPERATIONS_SESSION_SECRET": "\n" + "b" * 48 + " \n"})[0]
                         ["OPERATIONS_SESSION_SECRET"], "b" * 48)
        with self.assertRaisesRegex(ValueError, "multiple lines"):
            RENDER.runtime({**source, "OPERATIONS_JIRA_DELIVERY_TOKEN": "token\nINJECTED=x"})
        rsa = {**source, "OPERATIONS_GITHUB_APP_PRIVATE_KEY":
               "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----"}
        self.assertEqual(RENDER.runtime(rsa)[2], rsa["OPERATIONS_GITHUB_APP_PRIVATE_KEY"])

    def test_operations_apex_adoption_is_repository_discovered(self):
        workflow = (ROOT / ".github/workflows/control-plane-host.yaml").read_text()
        self.assertIn("domains/life2launch.dev/records?type=A&per_page=200", workflow)
        self.assertIn('select(.type == "A" and .name == "@")', workflow)
        self.assertIn("Expected exactly one legacy operations apex A record", workflow)
        self.assertIn("Operations apex was rolled back", workflow)
        self.assertIn("acknowledge_operations_rollback", workflow)
        rollback = (ROOT / ".github/workflows/rollback-operations-apex.yaml").read_text()
        self.assertIn("Expected exactly one operations apex A record", rollback)
        self.assertIn("refusing rollback", rollback)

    def test_accepts_isolated_matching_runtime(self):
        control, postgres, resolver = runtime()
        MODULE.validate(control, postgres, resolver)

    def test_rejects_dns_database_and_credential_boundary_failures(self):
        control, postgres, resolver = runtime()
        cases = (
            ("OPERATIONS_EXPECTED_IP", "143.110.225.232", "does not resolve"),
            ("OPERATIONS_DATABASE_URL", "postgresql+psycopg://operations:bad@localhost/operations", "private Compose"),
            ("JIRA_FEEDBACK_TOKEN", "delivery-token", "must be distinct"),
        )
        for name, value, message in cases:
            with self.subTest(name=name):
                candidate = dict(control)
                candidate[name] = value
                with self.assertRaisesRegex(ValueError, message):
                    MODULE.validate(candidate, postgres, resolver)

    def test_runtime_matches_versioned_launch_topology(self):
        control, postgres, resolver = runtime()
        topology = load_topology(ROOT / "deploy/environments/launch-lms.yaml")
        control["OPERATIONS_PUBLIC_URL"] = topology["operations"]["public_url"]
        control["OPERATIONS_DOMAIN"] = "life2launch.dev"
        MODULE.validate(control, postgres, resolver, topology)

    def test_nested_domains_require_host_only_cookies(self):
        topology = load_topology(ROOT / "deploy/environments/launch-lms.yaml")
        unsafe = deepcopy(topology)
        unsafe["application"]["cookie_scope"] = "shared-domain"
        with self.assertRaisesRegex(ValueError, "host-only"):
            validate_topology(unsafe)

    def test_control_plane_deployment_waits_for_approved_apex_cutover(self):
        topology = load_topology(ROOT / "deploy/environments/launch-lms.yaml")
        MODULE.require_operations_cutover(topology)
        missing = deepcopy(topology)
        del missing["dns"]["operations_cutover_evidence"]["backup_restore_run_id"]
        with self.assertRaisesRegex(ValueError, "backup_restore_run_id"):
            validate_topology(missing)
        paused = deepcopy(topology)
        paused["dns"]["operations_apex_cutover"] = False
        with self.assertRaisesRegex(ValueError, "not approved"):
            MODULE.require_operations_cutover(paused)

    def test_nested_cutover_rejects_unverified_cross_org_handoff(self):
        topology = load_topology(ROOT / "deploy/environments/launch-lms.yaml")
        unsafe = deepcopy(topology)
        unsafe["application"]["session_handoff_verified"] = False
        unsafe["application"]["unstable"]["cutover_approved"] = True
        with self.assertRaisesRegex(ValueError, "session handoff"):
            validate_topology(unsafe)

    def test_nested_cutover_requires_live_revision_evidence(self):
        topology = load_topology(ROOT / "deploy/environments/launch-lms.yaml")
        for field, message in (
            ("live_verification_run_id", "live verification run ID"),
            ("app_commit", "exact app commit"),
            ("infra_commit", "exact infra commit"),
        ):
            with self.subTest(field=field):
                unsafe = deepcopy(topology)
                del unsafe["application"]["unstable"]["cutover_evidence"][field]
                with self.assertRaisesRegex(ValueError, message):
                    validate_topology(unsafe)
