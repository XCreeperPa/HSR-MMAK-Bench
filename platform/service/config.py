"""Runtime configuration for the formal V2 test service.

Everything that differs between a laptop smoke run and a cloud deployment is
read from the environment. The server secret never lives inside the database:
it comes from ``HSR_SERVICE_SECRET`` or from a key file next to (not inside)
the SQLite file.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SERVICE_ROOT.parent


def _path_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


class Settings:
    """Resolved, immutable-ish settings object."""

    def __init__(self) -> None:
        self.db_path = _path_env('HSR_SERVICE_DB', SERVICE_ROOT / 'data' / 'service.db')
        self.media_dir = _path_env('HSR_SERVICE_MEDIA', SERVICE_ROOT / 'media')
        self.bank_dir = _path_env('HSR_SERVICE_BANK', REPO_ROOT / 'web')
        self.roster_path = _path_env('HSR_SERVICE_ROSTER', REPO_ROOT / 'web' / 'contestants.private.json')
        self.key_path = _path_env('HSR_SERVICE_KEY', SERVICE_ROOT / 'data' / 'server.key')

        # Total time budget for one full test, shared by human and agent.
        self.time_budget_seconds = int(os.environ.get('HSR_SERVICE_TIME_BUDGET', '3600'))
        # Login session lifetime. Expiry never re-issues a question or resets time.
        self.session_ttl_seconds = int(os.environ.get('HSR_SERVICE_SESSION_TTL', '28800'))
        # Failed code submissions allowed per IP before the IP is throttled.
        self.login_rate_limit = int(os.environ.get('HSR_SERVICE_LOGIN_RATE_LIMIT', '10'))
        self.login_rate_window_seconds = int(os.environ.get('HSR_SERVICE_LOGIN_RATE_WINDOW', '300'))
        # The six digit code space is finite; stop creating codes near exhaustion.
        self.code_space_warning = int(os.environ.get('HSR_SERVICE_CODE_SPACE_WARNING', '100000'))

        self.admin_username = os.environ.get('HSR_SERVICE_ADMIN_USER', 'admin')
        self.admin_password = os.environ.get('HSR_SERVICE_ADMIN_PASSWORD') or secrets.token_urlsafe(18)
        self.admin_password_is_generated = not os.environ.get('HSR_SERVICE_ADMIN_PASSWORD')

        self.host = os.environ.get('HSR_SERVICE_HOST', '127.0.0.1')
        self.port = int(os.environ.get('HSR_SERVICE_PORT', '8770'))
        self.public_base_url = os.environ.get('HSR_SERVICE_BASE_URL', '').rstrip('/')
        self.task_document_version = os.environ.get('HSR_SERVICE_TASK_DOC_VERSION', 'task-doc-v1')

    # ---------------------------------------------------------------- secrets
    def server_secret(self) -> bytes:
        """Return the server secret, creating a key file outside the database."""
        raw = os.environ.get('HSR_SERVICE_SECRET')
        if raw:
            return raw.encode('utf-8')
        if self.key_path.exists():
            return self.key_path.read_bytes()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_bytes(32)
        self.key_path.write_bytes(secret)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass
        return secret

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Only used by tests that need a different environment."""
    global _settings
    _settings = None
