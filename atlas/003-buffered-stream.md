# Failure 003: the buffered stream that fakes a fast first token

## Symptom

Every token is correct. Every SSE frame is correct. `data: [DONE]` is present. The first token arrives at the end.

A user watches a blank screen for four seconds, then the whole answer appears at once. The gateway says the model is fast because the gateway measures total request time, not token arrival.

## Minimal reproduction

Start the buffer fault proxy:

```bash
python3 mock_server_fixed.py --port 8010
python3 fault_proxy.py --mode buffer --port 8006 --upstream 8010
```

Measure arrival times:

```bash
python3 - <<'PY'
import json, time
import httpx

body = {
    "model": "mock-small",
    "messages": [{"role": "user", "content": "Why is the first token slow?"}],
    "max_tokens": 16,
    "stream": True,
}
arrivals = []
with httpx.stream("POST", "http://127.0.0.1:8006/v1/chat/completions", json=body) as r:
    for line in r.iter_lines():
        if line.startswith("data:") and '"content"' in line:
            arrivals.append(time.monotonic())

print("chunks:", len(arrivals))
print("span seconds:", round(arrivals[-1] - arrivals[0], 3))
PY
```

Observed:

```text
chunks: 16
span seconds: 0.0
```

Expected: a span of roughly `15 * decode_ms`, which is about 0.3 s at 20 ms per token.

The same stream sent directly to the mock has a positive span.

## Why a schema check misses it

The frames are byte-for-byte valid. The content is right. The finish reason is right. The usage is right.

The only broken promise is time. A validator that reads the response after it completes cannot see when each byte arrived. The client has to timestamp each read.

## Client impact

- TTFT becomes total latency, so the model looks slow.
- A streaming UI cannot show progress.
- A router that compares first-token latency across replicas sees every replica as equal.
- A timeout tuned for first-token arrival may cancel a healthy request.
- A benchmark that reads only the final body reports a fast stream when the user saw no stream.

## Test

```python
def test_stream_is_incremental(http, chat_body):
    body = chat_body(max_tokens=16, stream=True)
    arrivals = []

    with http.stream("POST", "/v1/chat/completions", json=body) as r:
        for line in r.iter_lines():
            if line.startswith("data:") and '"content"' in line:
                arrivals.append(time.monotonic())

    assert len(arrivals) >= 8
    assert arrivals[-1] - arrivals[0] > 0.15
```

The threshold depends on the mock's decode delay. With `MOCK_DECODE_MS=20`, a real stream spans about 0.3 s and a buffered stream spans about 0 s. Keep a margin.

## Fix

Do not read the upstream body into memory before writing it downstream. Forward each chunk as it arrives and flush:

```python
for chunk in upstream.iter_bytes():
    downstream.write(chunk)
    downstream.flush()
```

Also disable proxy buffering at the server and any reverse proxy in front of it. A correct application behind an nginx buffer still looks buffered to the user.

## Prevention

Timestamp every content frame. Assert a positive span. A test that only checks the final assembled text will never catch this.

## Related streaming failures

The same lesson applies to ordering:

- `duplicate-finish`: a second finish event makes clients that count events close the stream twice.
- `usage-before-finish`: usage before finish breaks clients that treat finish as the last content event.
- `cumulative-deltas`: each frame repeats the whole answer so far, so a client that concatenates deltas renders the answer many times.

All three are in the fault matrix. All three survive a schema check.
