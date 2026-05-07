"""Batch runner for Moonshine."""

from __future__ import annotations

from typing import Iterable, List

from moonshine.app import MoonshineApp


def run_batch(prompts: Iterable[str], *, home: str = "", mode: str = "research", project_slug: str = "general") -> List[str]:
    """Run a batch of prompts through Moonshine."""
    app = MoonshineApp(home=home or None)
    state = app.start_shell_state(mode=mode, project_slug=project_slug)
    outputs = [app.ask(prompt, state) for prompt in prompts]
    app.close_session(state)
    return outputs
