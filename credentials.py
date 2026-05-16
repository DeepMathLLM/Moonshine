"""Local runtime credential helpers for optional integrations."""

from __future__ import annotations

import os
from typing import Dict

from moonshine.utils import read_json, utc_now, write_json


def read_credentials(paths) -> Dict[str, object]:
    """Read the local credential file if it exists."""
    payload = read_json(paths.credentials_file, default={}) or {}
    if not isinstance(payload, dict):
        return {}
    secrets = payload.get("secrets", {})
    if not isinstance(secrets, dict):
        secrets = {}
    return {
        "version": int(payload.get("version", 1) or 1),
        "updated_at": str(payload.get("updated_at", "")),
        "secrets": {str(key): str(value) for key, value in secrets.items()},
    }


def write_credentials(paths, payload: Dict[str, object]) -> None:
    """Persist credentials with best-effort owner-only permissions on POSIX."""
    write_json(paths.credentials_file, payload)
    try:
        os.chmod(paths.credentials_file, 0o600)
    except OSError:
        pass


def get_stored_credential(paths, key: str) -> str:
    """Return a credential from the local credential file only."""
    payload = read_credentials(paths)
    return str(dict(payload.get("secrets") or {}).get(key, "") or "").strip()


def get_credential(paths, key: str, *, prefer_environment: bool = True) -> str:
    """Return a credential from environment or the local credential file."""
    key = str(key)
    if prefer_environment:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return get_stored_credential(paths, key)


def credential_source(paths, key: str) -> str:
    """Return the source label for a configured credential."""
    if os.environ.get(key, "").strip():
        return "environment"
    if get_stored_credential(paths, key):
        return "file"
    return ""


def set_credential(paths, key: str, value: str) -> None:
    """Store one credential in the local credential file."""
    key = str(key).strip()
    value = str(value).strip()
    if not key:
        raise ValueError("credential key cannot be empty")
    if not value:
        raise ValueError("%s cannot be empty" % key)
    payload = read_credentials(paths)
    secrets = dict(payload.get("secrets") or {})
    secrets[key] = value
    write_credentials(
        paths,
        {
            "version": 1,
            "updated_at": utc_now(),
            "secrets": secrets,
        },
    )


def load_credentials_into_environment(paths, *, override: bool = False) -> Dict[str, str]:
    """Load local credentials into this process environment."""
    loaded: Dict[str, str] = {}
    payload = read_credentials(paths)
    for key, value in dict(payload.get("secrets") or {}).items():
        key = str(key).strip()
        value = str(value).strip()
        if not key or not value:
            continue
        if override or not os.environ.get(key):
            os.environ[key] = value
            loaded[key] = value
    return loaded


def mask_secret(value: str) -> str:
    """Return a short non-sensitive display form."""
    value = str(value or "")
    if len(value) <= 8:
        return "***"
    return "%s...%s" % (value[:4], value[-4:])
