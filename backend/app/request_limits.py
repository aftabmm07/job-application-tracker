"""Bound mutation request bodies before routing, JSON parsing or DB dependencies."""
from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, send)
            return

        lengths = [value for key, value in scope["headers"] if key.lower() == b"content-length"]
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 10:
                await self.reject(scope, receive, send, 400, "Invalid request headers")
                return
            if int(lengths[0]) > self.max_bytes:
                await self.reject(scope, receive, send, 413, "Request body too large")
                return

        # Do not trust Content-Length: count streaming/chunked bytes as well.
        # Never append an over-limit chunk, or retain a list of empty chunks.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                await self.reject(scope, receive, send, 413, "Request body too large")
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        # Downstream receives only the bounded body. Subsequent calls still allow
        # normal disconnect handling by Starlette's response machinery.
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def reject(scope, receive, send, status, detail):
        # No request values in responses or logs. Do not drain rejected bodies.
        headers = {"Cache-Control": "no-store"}
        if scope.get("http_version", "1.1") in {"1.0", "1.1"}:
            headers["Connection"] = "close"
        response = JSONResponse({"detail": detail}, status_code=status, headers=headers)
        await response(scope, receive, send)
