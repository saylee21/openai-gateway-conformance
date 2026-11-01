# Failure 002: usage does not add up

## Symptom

A gateway returns:

```json
{
  "usage": {
    "prompt_tokens": 9,
    "completion_tokens": 16,
    "total_tokens": 16
  }
}
```

`total_tokens` equals the completion count. The prompt tokens disappeared.

## Minimal reproduction

```bash
curl -s http://127.0.0.1:8003/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mock-small",
    "messages": [{"role": "user", "content": "Why is the first token slow?"}],
    "max_tokens": 16
  }' | jq '.usage'
```

Observed:

```json
{"prompt_tokens": 9, "completion_tokens": 16, "total_tokens": 16}
```

Expected:

```json
{"prompt_tokens": 9, "completion_tokens": 16, "total_tokens": 25}
```

The `mangle-usage` fault proxy produces this on purpose.

## Why a schema check misses it

All three fields are integers. None is negative. The object has the right keys.

A schema check does not know that total is a sum. It also does not know that completion tokens should match the number of generated tokens.

## Client impact

- Cost dashboards undercount input tokens.
- Budget guards see less spend than the provider will bill.
- Capacity models underestimate prefill work.
- A cache hit ratio computed from prompt tokens is wrong.
- A client that uses `total_tokens` for context-window accounting can overflow the window.

## Test

```python
def test_usage_is_consistent(client):
    r = client.chat.completions.create(
        model="mock-small",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=8,
    )
    usage = r.usage
    assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
    assert usage.completion_tokens == len(r.choices[0].message.content.split())
```

The second assertion is deliberately strict for this mock. The mock emits one word per token, so the answer length and the count must agree. A real tokenizer would need a token count, not `split()`.

The streaming path needs its own check:

```python
def test_stream_chunks(client):
    ...
    assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
```

Some gateways compute usage only for non-streaming requests and put `null` or a stale value in the final stream chunk.

## Fix

Compute usage once, at the end of the request, from the actual tokenizer:

```python
usage = {
    "prompt_tokens": prompt_tokens,
    "completion_tokens": completion_tokens,
    "total_tokens": prompt_tokens + completion_tokens,
}
```

Do not copy `completion_tokens` into `total_tokens`. Do not estimate prompt tokens from characters unless the response says it is an estimate.

## Prevention

Check both paths. Check both the sum and the completion count. A total that adds up can still use the wrong completion count.
