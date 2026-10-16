"""
Logging setup: one line per event, always tagged with request id and user id.

Plain key=value text (not JSON) because it is readable both in your terminal
and in Render's log viewer, and still easy to grep.
"""

import logging
import sys

from app.core.request_context import request_id_var, user_id_var


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s req=%(request_id)s user=%(user_id)s %(message)s"
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Our own access log (middleware) replaces uvicorn's, which lacks request ids.
    logging.getLogger("uvicorn.access").disabled = True
