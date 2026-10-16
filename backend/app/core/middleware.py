"""
Request middleware: request id, access log, security headers.

Written as plain ASGI middleware (not Starlette's BaseHTTPMiddleware), which
is faster and does not break streaming responses such as file exports.
"""

import json
import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_body
from app.core.request_context import request_id_var, user_id_var

logger = logging.getLogger("app.access")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cache-control", b"no-store"),
]


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Accept a caller-supplied id only if it is short and harmless.
        incoming = ""
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex

        rid_token = request_id_var.set(request_id)
        uid_token = user_id_var.set("-")
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                existing = {h[0].lower() for h in headers}
                headers.extend(h for h in _SECURITY_HEADERS if h[0] not in existing)
                message["headers"] = headers
            await send(message)

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send_wrapper(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            # Unexpected crash. Handled here (not by Starlette's outermost
            # handler) so the 500 still carries the request id and the log
            # line is tagged with it. Clients never see internal details.
            logger.exception("unhandled error: %s", type(exc).__name__)
            if response_started:
                raise  # too late to send a clean error; let the server close it
            body = json.dumps(
                error_body("internal_error", "Something went wrong on our side.")
            ).encode()
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send_wrapper({"type": "http.response.body", "body": body})
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            path = scope.get("path", "")
            if path not in ("/health",):  # keep health-check pings out of the log
                logger.info(
                    "%s %s -> %s in %.1fms",
                    scope.get("method", "-"),
                    path,
                    status_holder["status"],
                    elapsed_ms,
                )
            request_id_var.reset(rid_token)
            user_id_var.reset(uid_token)
