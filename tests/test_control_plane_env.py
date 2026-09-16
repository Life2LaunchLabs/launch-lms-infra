"""Runtime deployment contract for the dedicated operations host."""

import importlib.util
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("control_plane_env", ROOT / "scripts/validate-control-plane-env.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
from environment_topology import load_topology, validate_topology


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
        with self.assertRaisesRegex(ValueError, "not approved"):
            MODULE.require_operations_cutover(topology)
        approved = deepcopy(topology)
        approved["dns"]["operations_apex_cutover"] = True
        MODULE.require_operations_cutover(approved)
