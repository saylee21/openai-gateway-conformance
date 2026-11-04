# Report 003: LiteLLM 1.108.0 re-run

Run date: 2026-11-04

## Why this report

Report 002 recorded four gaps in LiteLLM 1.106.0 on 2026-11-01. Before opening
upstream issues, I re-ran the same suite against the latest release to check
whether the findings were stale.

They are not stale.

## Environment

- macOS, arm64
- Python 3.12.13
- LiteLLM 1.108.0
- `openai` 3.16.2
- `pytest` 9.2.0
- `httpx` 0.28.1
- No GPU, no Docker

## Setup

```bash
# Terminal 1: the fixed mock
python3 mock_server_fixed.py --port 8010

# Terminal 2: LiteLLM in front of the mock
LITELLM_MASTER_KEY=sk-local-demo \
OPENAI_API_KEY=test \
litellm --config litellm.config.example.yaml --port 4000

# Terminal 3: the suite
MOCK_BASE_URL=http://127.0.0.1:4000 \
MOCK_API_KEY=sk-local-demo \
pytest -v
```

`sk-local-demo` is a fake placeholder, not a credential.

## Result

```text
15 passed, 4 failed in 18.34s
```

Failing tests:

```text
test_error_shape_404
test_unknown_path_is_json_404
test_request_id_is_echoed
test_sdk_raises_typed_error
```

These are the same four tests that failed on 1.106.0. They represent three
root causes, because `test_error_shape_404` and `test_sdk_raises_typed_error`
share one cause: LiteLLM returns 400 instead of 404 for an unknown model.

## What this means

The findings in Report 002 remain valid on the latest release. The next step
is to open three upstream issues, not four:

1. Unknown model returns 400, not 404, so the OpenAI SDK raises
   `BadRequestError` instead of `NotFoundError`.
2. Unknown path returns FastAPI `{"detail":"Not Found"}` instead of the
   OpenAI error envelope.
3. The client's `X-Request-Id` is not echoed under the same header.

Each issue should include a minimal reproduction, the expected and observed
behavior, the client impact, and an offer to send a test. If the behavior is
intentional, the maintainers should document it, and the atlas should record
that response.

## Status

No upstream issue or pull request has been opened from this report yet. The
report is the evidence. Any issue should link back here and to Report 002.

## Reproduce

The commands above are the full reproduction. The suite does not retry. A
failure is a finding, not a flake. Re-run once to confirm, then record the
endpoint, version, request, and response.
