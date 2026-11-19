"""FastAPI web app: browse, compare and triage the log sets of a store.

Needs the ``web`` extra (``uv sync --extra web``). Run it with ``ragnalog serve``
or build it yourself::

    from ragnalog.webapp import Settings, create_app
    app = create_app(Settings(store=Path(".ragnalog")))

Depends on ``core``, ``store``, ``report`` and ``service``, never on ``cli``
(``GET /api/manifest`` serves the generated ``service/manifest.json``).
"""

from ragnalog.webapp.app import create_app
from ragnalog.webapp.settings import Settings

__all__ = ["Settings", "create_app"]
