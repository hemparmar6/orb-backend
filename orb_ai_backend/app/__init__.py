"""ORB AI backend package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orb-ai-backend")
except PackageNotFoundError:  # pragma: no cover - not installed as a package
    __version__ = "1.2.3"
