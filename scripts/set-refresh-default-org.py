"""Align refreshed runtime routing with the restored database's owner org."""

import os
from pathlib import Path
import re
import sys

from env_file import read_env


def set_default_org(path: Path, slug: str) -> None:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Restored owner organization has an invalid slug")

    env = read_env(path)
    env["NEXT_PUBLIC_LAUNCHLMS_DEFAULT_ORG"] = slug
    for key, value in env.items():
        if any(character in value for character in "\n\r'") or "${" in value:
            raise ValueError(f"Unsupported dotenv value for {key}")

    os.umask(0o077)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(f"{key}='{value}'\n" for key, value in env.items()))
    temporary.replace(path)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: set-refresh-default-org.py REFRESH_ENV OWNER_ORG_SLUG")
    if Path(".deployment-environment").read_text().strip() != "unstable":
        raise SystemExit("Refusing to alter a non-unstable environment")
    target = Path(sys.argv[1])
    if target.resolve() != Path(".deploy-state/refresh.env").resolve():
        raise SystemExit("Only the prepared refresh environment may be changed")
    set_default_org(target, sys.argv[2])
