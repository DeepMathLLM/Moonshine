"""Compatibility export for the Moonshine CLI shell."""

from moonshine.moonshine_cli.main import MoonshineCLI

MoonshineREPL = MoonshineCLI

__all__ = ["MoonshineCLI", "MoonshineREPL"]
