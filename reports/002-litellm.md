# Report 002: LiteLLM 1.106.0

Run date: 2026-11-01

## Setup

The suite was pointed at a real gateway, not at the mock:

```text
client -> LiteLLM 1.106.0 on :4000 -> mock_server_fixed.py on :8010
```

LiteLLM was started from a local clone with a model alias:

```yaml
model_list:
  - model_name: mock-small
    litellm_params:
      model: openai/mock-small
      api_base: http://127.0.0.1:8010/v1
      api_key: test

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

```bash
LITELLM_MASTER_KEY=sk-local \
OPENAI_API_KEY=test \
litellm --config litellm.config.yaml --port 4000
```

```bash
MOCK_BASE_URL=http://127.0.0.1:4000 \
MOCK_API_KEY=sk-local \
pytest -v
```

## Result

```text
15 passed, 4 failed in 6.29s
```

Passing:

- model listing and raw list shape
- completion shape
- usage sum
- `length` and `stop` finish reasons
- unique ids
- malformed JSON error shape
- streaming chunks, `[DONE]`, incremental timing
- streaming finish reason
- one finish event
- usage after finish
- non-cumulative deltas

Failing:

```text
test_error_shape_404
test_unknown_path_is_json_404
test_request_id_is_echoed
test_sdk_raises_typed_error
```

## Findings

### 1. Invalid model returns 400, not 404

Request:

```bash
curl -s -i \
  -H "Authorization: Bearer sk-local" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-9","messages":[{"role":"user","content":"Hello"}],"max_tokens":4}' \
  http://127.0.0.1:4000/v1/chat/completions
```

Observed:

```text
HTTP/1.1 400 Bad Request
{"error":{"message":"/chat/completions: Invalid model name passed in model=gpt-9. Call `/v1/models` to view available models for your key.","type":"invalid_request_error","param":null,"code":null}}
```

The suite expects `404` with `code: model_not_found` and `param: model`, which is the OpenAI error contract.

Why it matters: an SDK maps `404` to `NotFoundError` and `400` to `BadRequestError`. A client that handles a missing model as a configuration error will now treat it as a malformed request. The typed-error test fails for the same root cause, so this is one defect with two visible symptoms.

Severity: medium. The error is still JSON and still has a message.

### 2. Unknown path returns a FastAPI error, not an OpenAI error

Request:

```bash
curl -s -i \
  -H "Authorization: Bearer sk-local" \
  http://127.0.0.1:4000/v1/does-not-exist
```

Observed:

```text
HTTP/1.1 404 Not Found
{"detail":"Not Found"}
```

Expected:

```json
{"error": {"message": "...", "type": "invalid_request_error", "param": null, "code": null}}
```

Why it matters: clients that branch on `body["error"]["type"]` crash on a gateway 404. This is the difference between a gateway that speaks OpenAI and a gateway that only speaks OpenAI on the happy path.

Severity: low to medium. The status code is right, the body is not.

### 3. `X-Request-Id` is not echoed

Request:

```bash
curl -s -i \
  -H "Authorization: Bearer sk-local" \
  -H "X-Request-Id: req_probe_001" \
  -H "Content-Type: application/json" \
  -d '{"model":"mock-small","messages":[{"role":"user","content":"Hello"}],"max_tokens":4}' \
  http://127.0.0.1:4000/v1/chat/completions
```

Observed response headers include:

```text
x-litellm-call-id: fc96decc-8298-445c-8fbb-848cc7224015
request-id: req_592e57aa2cf8
llm_provider-x-request-id: req_592e57aa2cf8
llm_provider-request-id: req_592e57aa2cf8
```

Observed response headers do not include:

```text
x-request-id: req_probe_001
```

LiteLLM keeps its own call id and forwards the upstream request id under a prefixed header. It does not return the client's id under the OpenAI header.

Why it matters: the client cannot join its request log to the gateway log with the id it sent. The OpenAI SDK also reads `x-request-id` for `_request_id`. A support workflow that asks the user for the request id gets nothing useful.

Severity: medium. The information exists, but not under the contract header.

### 4. SDK raises the wrong typed error

This is the client-side symptom of finding 1:

```python
from openai import NotFoundError

client.chat.completions.create(model="gpt-9", ...)
```

Observed:

```text
openai.BadRequestError: Error code: 400
```

Expected:

```text
openai.NotFoundError
```

The suite catches this separately because typed errors are part of the client contract, not just the HTTP status.

## What is not a finding

The stream itself is correct in this run:

- one finish event
- usage after finish
- `[DONE]` at the end
- deltas that reassemble to the non-streamed answer
- timing that shows real incremental delivery

The gateway is not buffering the stream. That is the failure this suite was built to catch, and LiteLLM passed it.

## Status

No upstream issue or pull request was opened from this report. The findings are recorded here first. Any upstream change should include a failing test and should be discussed with the maintainers before a PR.

## Reproduce

```bash
git clone https://github.com/saylee21/openai-gateway-conformance
cd openai-gateway-conformance
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Terminal 1
python3 mock_server_fixed.py --port 8010

# Terminal 2: start LiteLLM with the config above on port 4000

# Terminal 3
MOCK_BASE_URL=http://127.0.0.1:4000 MOCK_API_KEY=sk-local pytest -v
```
