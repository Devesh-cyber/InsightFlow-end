"""
Per-request context (request id, user id) available anywhere via contextvars.

contextvars work across async code and are copied into the threadpool that
FastAPI uses for sync endpoints, so log lines from deep inside the data engine
still carry the request id.
"""

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")
