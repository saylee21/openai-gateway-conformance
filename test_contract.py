"""
OpenAI gateway contract tests.

Two clients, deliberately:

  - the OpenAI Python SDK, because that is what real users run
  - raw httpx, because the SDK hides framing, headers and error bodies

Run against a server:
  MOCK_BASE_URL=http://127.0.0.1:8010 pytest -v

The suite is written so that each fault has one obvious catcher.
"""

import json
import time

import pytest
from openai import NotFoundError


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------

def test_models_list(client):
    models = client.models.list()
    ids = [m.id for m in models.data]
    assert "mock-small" in ids


def test_models_list_raw(http):
    r = http.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert any(m["id"] == "mock-small" for m in body["data"])


# ---------------------------------------------------------------------------
# Non-streaming completion shape
# ---------------------------------------------------------------------------

def test_completion_shape(client):
    r = client.chat.completions.create(
        model="mock-small",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=8,
        temperature=0,
    )
    assert r.object == "chat.completion"
    assert r.model == "mock-small"
    assert r.id.startswith("chatcmpl-")
    assert len(r.choices) == 1
    choice = r.choices[0]
    assert choice.index == 0
    assert choice.message.role == "assistant"
    assert isinstance(choice.message.content, str)
    assert choice.message.content.strip()
    assert choice.finish_reason in ("stop", "length")


def test_usage_is_consistent(client):
    r = client.chat.completions.create(
        model="mock-small",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=8,
        temperature=0,
    )
    usage = r.usage
    assert usage is not None
    assert usage.prompt_tokens > 0
    assert usage.completion_tokens > 0
    assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
    assert usage.completion_tokens == len(r.choices[0].message.content.split())


def test_finish_reason_length(client):
    r = client.chat.completions.create(
        model="mock-small",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=5,
        temperature=0,
    )
    assert r.choices[0].finish_reason == "length"


def test_finish_reason_stop(client):
    r = client.chat.completions.create(
        model="mock-small",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=40,
        temperature=0,
    )
    assert r.choices[0].finish_reason == "stop"


def test_ids_are_unique(client):
    first = client.chat.completions.create(
        model="mock-small",
        messages=[{"role": "user", "content": "One"}],
        max_tokens=4,
    )
    second = client.chat.completions.create(
        model="mock-small",
        messages=[{"role": "user", "content": "Two"}],
        max_tokens=4,
    )
    assert first.id != second.id


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def test_error_shape_404(http, chat_body):
    r = http.post("/v1/chat/completions", json=chat_body(model="gpt-9"))
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert "error" in body
    err = body["error"]
    assert err["type"] == "invalid_request_error"
    assert err["code"] == "model_not_found"
    assert err["param"] == "model"
    assert "gpt-9" in err["message"]


def test_error_shape_bad_json(http):
    r = http.post(
        "/v1/chat/completions",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"]["type"] == "invalid_request_error"


def test_unknown_path_is_json_404(http):
    r = http.get("/v1/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert "error" in r.json()


# ---------------------------------------------------------------------------
# Request id
# ---------------------------------------------------------------------------

def test_request_id_is_echoed(http, chat_body):
    sent = "req_day2_fixed_001"
    r = http.post(
        "/v1/chat/completions",
        json=chat_body(max_tokens=4),
        headers={"X-Request-Id": sent},
    )
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == sent


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

def test_stream_chunks(client):
    stream = client.chat.completions.create(
        model="mock-small",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=40,
        temperature=0,
        stream=True,
        stream_options={"include_usage": True},
    )

    content = []
    finishes = []
    roles = []
    usage = None
    ids = set()

    for chunk in stream:
        ids.add(chunk.id)
        if chunk.choices:
            choice = chunk.choices[0]
            if choice.delta.role:
                roles.append(choice.delta.role)
            if choice.delta.content:
                content.append(choice.delta.content)
            if choice.finish_reason:
                finishes.append(choice.finish_reason)
        if chunk.usage:
            usage = chunk.usage

    assert len(ids) == 1
    assert roles and roles[0] == "assistant"
    assert "".join(content).strip()
    assert finishes == ["stop"]
    assert usage is not None
    assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens


def test_stream_ends_with_done(http, chat_body):
    body = chat_body(max_tokens=6, stream=True, stream_options={"include_usage": True})
    with http.stream("POST", "/v1/chat/completions", json=body) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        data_lines = [line for line in r.iter_lines() if line.startswith("data:")]

    assert data_lines, "no data lines in the stream"
    assert data_lines[-1].strip() == "data: [DONE]"


def test_stream_is_incremental(http, chat_body):
    body = chat_body(max_tokens=16, stream=True)
    arrivals = []

    with http.stream("POST", "/v1/chat/completions", json=body) as r:
        for line in r.iter_lines():
            if line.startswith("data:") and '"content"' in line:
                arrivals.append(time.monotonic())

    assert len(arrivals) >= 8, "expected one content chunk per token"
    span = arrivals[-1] - arrivals[0]
    assert span > 0.15, "content chunks arrived together, the stream was buffered"


def test_stream_finish_reason_matches_cut(http, chat_body):
    body = chat_body(max_tokens=5, stream=True)
    finishes = []

    with http.stream("POST", "/v1/chat/completions", json=body) as r:
        for line in r.iter_lines():
            if not line.startswith("data: {"):
                continue
            chunk = json.loads(line[len("data: "):])
            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    finishes.append(choice["finish_reason"])

    assert finishes == ["length"]


def _stream_events(http, chat_body, **overrides):
    body = chat_body(stream=True, **overrides)
    events = []
    with http.stream("POST", "/v1/chat/completions", json=body) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: {"):
                events.append(json.loads(line[len("data: "):]))
    return events


def test_stream_has_one_finish(http, chat_body):
    events = _stream_events(
        http,
        chat_body,
        max_tokens=40,
        stream_options={"include_usage": True},
    )

    finishes = [
        choice["finish_reason"]
        for event in events
        for choice in (event.get("choices") or [])
        if choice.get("finish_reason")
    ]

    assert finishes == ["stop"], (
        "a stream must carry exactly one finish_reason, got %r" % finishes
    )


def test_stream_usage_comes_after_finish(http, chat_body):
    events = _stream_events(
        http,
        chat_body,
        max_tokens=12,
        stream_options={"include_usage": True},
    )

    finish_at = None
    usage_at = None
    for i, event in enumerate(events):
        if any(c.get("finish_reason") for c in (event.get("choices") or [])):
            finish_at = i
        if event.get("usage"):
            usage_at = i

    assert finish_at is not None, "stream never sent finish_reason"
    assert usage_at is not None, "stream never sent usage"
    assert usage_at > finish_at, (
        "usage arrived before the finish event: usage=%d finish=%d"
        % (usage_at, finish_at)
    )


def test_stream_deltas_are_not_cumulative(http, chat_body):
    events = _stream_events(http, chat_body, max_tokens=16)

    deltas = []
    for event in events:
        for choice in (event.get("choices") or []):
            text = (choice.get("delta") or {}).get("content")
            if text:
                deltas.append(text)

    assert len(deltas) >= 8, "expected one content delta per token"

    baseline = http.post(
        "/v1/chat/completions",
        json=chat_body(max_tokens=16),
    )
    assert baseline.status_code == 200
    expected = baseline.json()["choices"][0]["message"]["content"]

    assert "".join(deltas) == expected, (
        "streamed deltas do not reassemble to the non-streamed answer; "
        "a cumulative gateway repeats text"
    )


def test_sdk_raises_typed_error(client):
    with pytest.raises(NotFoundError):
        client.chat.completions.create(
            model="gpt-9",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=4,
        )
