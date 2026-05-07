"""Gateway session helpers."""

from __future__ import annotations


class GatewaySessionStore(object):
    """Thin adapter over the core session store."""

    def __init__(self, session_store):
        self.session_store = session_store

    def create(self, mode: str, project_slug: str) -> str:
        """Create a session for a gateway conversation."""
        return self.session_store.create_session(mode, project_slug)
