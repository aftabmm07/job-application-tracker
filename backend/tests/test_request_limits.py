"""Send real ASGI chunks, rather than relying on TestClient's buffered stream."""
import asyncio
import json

import pytest

from app.request_limits import RequestBodyLimitMiddleware


def exchange(chunks, *, headers=(), method="POST", limit=16, disconnect=False, http_version="1.1"):
    messages, delivered = [], []
    reads = 0

    async def receive():
        nonlocal reads
        index = reads
        reads += 1
        if index >= len(chunks):
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": chunks[index],
                "more_body": disconnect or index < len(chunks) - 1}

    async def send(message):
        messages.append(message)

    async def app(scope, receive, send):
        delivered.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    scope = {"type": "http", "method": method, "path": "/auth/login",
             "headers": list(headers), "http_version": http_version}
    asyncio.run(RequestBodyLimitMiddleware(app, limit)(scope, receive, send))
    return messages, delivered, reads


def assert_rejected(messages, delivered, status):
    assert not delivered
    assert messages[0]["status"] == status
    assert (b"cache-control", b"no-store") in messages[0]["headers"]
    assert b"private-marker" not in messages[1]["body"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_oversized_content_length_rejected_without_reading(method):
    messages, delivered, reads = exchange([b"private-marker"], method=method,
                                        headers=[(b"content-length", b"17")])
    assert_rejected(messages, delivered, 413)
    assert reads == 0
    assert (b"connection", b"close") in messages[0]["headers"]


@pytest.mark.parametrize("headers", [[], [(b"transfer-encoding", b"chunked")],
                                     [(b"content-length", b"1")]])
def test_streaming_limit_counts_bytes_and_stops_at_first_excess(headers):
    messages, delivered, reads = exchange([b"12345678", b"12345678", b"!", b"unread"], headers=headers)
    assert_rejected(messages, delivered, 413)
    assert reads == 3
    assert json.loads(messages[1]["body"]) == {"detail": "Request body too large"}


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"16")],
                                     [(b"transfer-encoding", b"chunked")]])
def test_exact_boundary_and_empty_chunks_replay_correctly(headers):
    messages, delivered, reads = exchange([b"", b"12345678", b"", b"12345678"], headers=headers)
    assert messages[0]["status"] == 200
    assert delivered == [{"type": "http.request", "body": b"1234567812345678", "more_body": False}]
    assert reads == 4


@pytest.mark.parametrize("headers", [
    [(b"content-length", b"private-marker")], [(b"content-length", b"-1")],
    [(b"content-length", b"9" * 100)], [(b"content-length", b"1"), (b"content-length", b"1")],
])
def test_invalid_or_duplicate_lengths_fail_safely(headers):
    messages, delivered, reads = exchange([b"x"], headers=headers)
    assert_rejected(messages, delivered, 400)
    assert reads == 0


def test_disconnect_during_upload_does_not_call_app():
    messages, delivered, reads = exchange([b"small"], disconnect=True)
    assert messages == delivered == []
    assert reads == 2


def test_empty_mutation_body_is_preserved():
    messages, delivered, _ = exchange([b""], method="DELETE")
    assert messages[0]["status"] == 200
    assert delivered[0]["body"] == b""


def test_http2_rejection_omits_connection_header():
    messages, delivered, _ = exchange([b"x" * 17], http_version="2")
    assert_rejected(messages, delivered, 413)
    assert b"connection" not in dict(messages[0]["headers"])
