"""Environment-only control-plane configuration."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("OPERATIONS_DATABASE_URL", "postgresql+psycopg://operations@postgres/operations")
    public_url: str = os.getenv("OPERATIONS_PUBLIC_URL", "http://localhost:8080")
    github_client_id: str = os.getenv("GITHUB_OAUTH_CLIENT_ID", "")
    github_client_secret: str = os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "")
    github_org: str = os.getenv("GITHUB_ALLOWED_ORG", "Life2LaunchLabs")
    github_repo: str = os.getenv("GITHUB_ALLOWED_REPO", "Life2LaunchLabs/launch-lms")
    github_app_id: str = os.getenv("GITHUB_APP_ID", "")
    github_installation_id: str = os.getenv("GITHUB_APP_INSTALLATION_ID", "")
    github_app_private_key: str = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
    jira_base_url: str = os.getenv("JIRA_BASE_URL", "")
    jira_delivery_email: str = os.getenv("JIRA_DELIVERY_EMAIL", "")
    jira_delivery_token: str = os.getenv("JIRA_DELIVERY_TOKEN", "")
    jira_feedback_email: str = os.getenv("JIRA_FEEDBACK_EMAIL", "")
    jira_feedback_token: str = os.getenv("JIRA_FEEDBACK_TOKEN", "")
    session_secret: str = os.getenv("OPERATIONS_SESSION_SECRET", "")
    symphony_status_url: str = os.getenv("SYMPHONY_STATUS_URL", "http://symphony:8788/api/v1/state")
    environment: str = os.getenv("OPERATIONS_ENVIRONMENT", "development")
    read_only: bool = os.getenv("OPERATIONS_READ_ONLY", "true").lower() == "true"

    def require_auth(self) -> None:
        if not self.github_client_id or not self.github_client_secret or len(self.session_secret) < 32:
            raise RuntimeError("GitHub OAuth and a 32+ character session secret are required")
