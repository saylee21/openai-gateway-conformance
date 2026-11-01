# Failure 001: the cut answer says `stop`

## Symptom

A request sets `max_tokens=5`. The model is cut off after five tokens. The response says:

```json
{
  "choices": [
    {
      "message": {"role": "assistant", "content": "Prefill read 1 prompt"},
      "finish_reason": "stop"
    }
  ]
}
```

The text is visibly incomplete. The field says the model finished naturally.

## Minimal reproduction

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mock-small",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 5
  }' | jq '.choices[0].finish_reason'
```

Observed: `"stop"`

Expected: `"length"`

The buggy mock has this defect on purpose. The fixed mock returns `length`.

## Why a schema check misses it

The JSON is valid. `finish_reason` is a string. It is one of the allowed enum values. Nothing about the shape is wrong.

The lie is semantic: the field does not describe what happened to the generation. A validator that checks types and enums will pass this response.

## Client impact

- An agent loop sees `stop` and treats a partial answer as complete.
- A retry layer does not retry, because `stop` is a successful terminal state.
- A UI hides the "truncated" warning.
- A billing or evaluation pipeline records a partial answer as a full one.
- A streaming client that only checks the last chunk gets the same lie.

## Test

```python
def test_finish_reason_length(client):
    r = client.chat.completions.create(
        model="mock-small",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=5,
    )
    assert r.choices[0].finish_reason == "length"
```

The streaming path needs its own test because some gateways rewrite the non-streaming body correctly and lose the field while translating SSE:

```python
def test_stream_finish_reason_matches_cut(http, chat_body):
    body = chat_body(max_tokens=5, stream=True)
    finishes = []
    with http.stream("POST", "/v1/chat/completions", json=body) as r:
        for line in r.iter_lines():
            if line.startswith("data: {"):
                chunk = json.loads(line[6:])
                for choice in chunk.get("choices") or []:
                    if choice.get("finish_reason"):
                        finishes.append(choice["finish_reason"])
    assert finishes == ["length"]
```

## Fix

The server must decide the reason at the point where it stops:

```python
finish_reason = "length" if len(tokens) < len(full_answer) else "stop"
```

Do not infer it from the HTTP status. Do not default it to `stop`.

## Prevention

Run the non-streaming and streaming cases. A gateway can be correct in one path and wrong in the other. The buggy baseline in this repo fails three tests from two seeded defects for exactly that reason.
