"""Verify the protected Spaces state bucket with least-privilege credentials."""

from __future__ import annotations

import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


def main() -> int:
    try:
        bucket = required_environment("STATE_BUCKET")
        access_key = required_environment("AWS_ACCESS_KEY_ID")
        secret_key = required_environment("AWS_SECRET_ACCESS_KEY")
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 2

    region = "sfo3"
    client = boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://{region}.digitaloceanspaces.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )

    try:
        status = client.get_bucket_versioning(Bucket=bucket).get("Status")
    except (BotoCoreError, ClientError) as error:
        print(f"Unable to read state-bucket versioning: {error}", file=sys.stderr)
        return 1

    if status != "Enabled":
        print("State bucket versioning must be Enabled before plan/apply", file=sys.stderr)
        return 1

    print(f"Verified versioning for private state bucket {bucket} in {region}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
