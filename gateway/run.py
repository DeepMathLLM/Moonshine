"""Minimal gateway runner scaffold."""

from __future__ import annotations

from moonshine.app import MoonshineApp, ShellState


class GatewayRunner(object):
    """Dispatch inbound messages into the Moonshine core."""

    def __init__(self, app: MoonshineApp):
        self.app = app

    def handle_message(self, message: str, *, mode: str = "chat", project_slug: str = "general") -> str:
        """Process one gateway message."""
        state = ShellState(
            mode=mode,
            project_slug=project_slug,
            session_id=self.app.session_store.create_session(mode, project_slug),
        )
        reply = self.app.ask(message, state)
        self.app.close_session(state)
        return reply
