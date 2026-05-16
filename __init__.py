"""Moonshine package."""

__version__ = "0.1.0"

__all__ = ["MoonshineApp"]


def __getattr__(name):
    """Lazily expose compatibility exports."""
    if name == "MoonshineApp":
        from moonshine.app import MoonshineApp

        return MoonshineApp
    raise AttributeError(name)
