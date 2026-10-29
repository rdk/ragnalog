"""atlas-log-analyzer: parse, group and compare application log files."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("atlas-log-analyzer")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0+unknown"
