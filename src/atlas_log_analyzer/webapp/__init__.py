"""FastAPI web app: browse, compare and triage the log sets of a store.

Needs the ``web`` extra (``uv sync --extra web``). Run it with ``atlas-log serve``
or build it yourself::

    from atlas_log_analyzer.webapp import Settings, create_app
    app = create_app(Settings(store=Path(".atlas-log")))

Depends on ``core``, ``store``, ``report`` and ``service``; on ``cli`` only for
``GET /api/manifest``, which describes the CLI's commands.
"""

from atlas_log_analyzer.webapp.app import create_app
from atlas_log_analyzer.webapp.settings import Settings

__all__ = ["Settings", "create_app"]
