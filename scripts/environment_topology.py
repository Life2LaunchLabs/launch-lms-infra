"""Load and validate versioned, non-secret deployment topology."""

from __future__ import annotations

import ipaddress
from pathlib import Path
import re
from urllib.parse import urlparse

import yaml


HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def load_topology(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_topology(value)
    return value


def _https_host(value: str, field: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/"):
        raise ValueError(f"{field} must be an HTTPS origin without a path")
    return parsed.hostname


def validate_topology(value: object) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("environment topology schema_version must be 1")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", value.get("project_id", "")):
        raise ValueError("environment topology project_id must be a lowercase slug")

    operations = value.get("operations", {})
    operations_host = _https_host(operations.get("public_url", ""), "operations.public_url")
    ipaddress.ip_address(operations.get("expected_ipv4", ""))

    application = value.get("application", {})
    if application.get("cookie_scope") != "host-only":
        raise ValueError("nested hosted environments require host-only cookies")
    if not isinstance(application.get("session_handoff_verified"), bool):
        raise ValueError("tenant session handoff verification must be an explicit boolean")
    production = application.get("production", {})
    unstable = application.get("unstable", {})
    if not isinstance(unstable.get("cutover_approved"), bool):
        raise ValueError("nested unstable cutover must be explicitly approved")
    if unstable["cutover_approved"] and not application["session_handoff_verified"]:
        raise ValueError("nested unstable cutover requires verified tenant session handoff")
    production_domain = production.get("base_domain", "")
    unstable_domain = unstable.get("base_domain", "")
    if not HOST.fullmatch(production_domain) or not HOST.fullmatch(unstable_domain):
        raise ValueError("application base domains must be DNS hostnames")
    if unstable_domain != f"unstable.{production_domain}":
        raise ValueError("unstable must be the unstable subdomain of the production base domain")
    if production.get("origin_template") != f"https://{{organization}}.{production_domain}":
        raise ValueError("production origin template must match its base domain")
    if unstable.get("origin_template") != f"https://{{organization}}.{unstable_domain}":
        raise ValueError("unstable origin template must match its base domain")
    if unstable.get("operations_surface_url") != operations.get("public_url"):
        raise ValueError("unstable operations surface must use the configured control plane")

    dns = value.get("dns", {})
    ipaddress.ip_address(dns.get("unstable_ipv4", ""))
    if dns.get("app_zone") != production_domain or dns.get("operations_zone") != operations_host:
        raise ValueError("DNS zones must match the application and operations domains")
    if dns.get("records", {}).get("operations") != "@":
        raise ValueError("operations must use the configured .dev zone apex")
    if dns.get("records", {}).get("unstable_apex") != "unstable":
        raise ValueError("unstable apex DNS record must be named unstable")
    if dns.get("records", {}).get("unstable_wildcard") != "*.unstable":
        raise ValueError("nested tenant wildcard DNS record must be named *.unstable")
    if not isinstance(dns.get("operations_apex_cutover"), bool):
        raise ValueError("operations apex cutover must be an explicit boolean")
    if dns["operations_apex_cutover"] and not unstable["cutover_approved"]:
        raise ValueError("operations apex cutover requires approved nested unstable")
