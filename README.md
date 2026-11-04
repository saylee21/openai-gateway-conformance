# OpenAI Gateway Conformance

![conformance: mock 19/19, faults 10/10](badge.svg)

[![conformance](https://github.com/saylee21/openai-gateway-conformance/actions/workflows/conformance.yml/badge.svg)](https://github.com/saylee21/openai-gateway-conformance/actions/workflows/conformance.yml)

A CPU-first contract suite for OpenAI-compatible LLM gateways.

Most gateway tests check that the JSON parses. This suite checks the promises a client actually depends on: a cut answer says `length`, usage adds up, request ids survive the hop, a stream has one finish event, usage arrives after finish, deltas are deltas, and a buffered proxy cannot fake a fast first token.

**Current result:** 19 of 19 tests pass against the contract-correct mock. 10 of 10 injected fault modes are caught. LiteLLM 1.108.0 passes 15 of 19 and exposes the same four gaps first recorded on 1.106.0. See [reports/001-mock.md](reports/001-mock.md), [reports/002-litellm.md](reports/002-litellm.md), and [reports/003-litellm-1.108.md](reports/003-litellm-1.108.md).

This is not a certification. It is a reproducible way to find a gateway that is lying politely.

## What the suite checks

| Area | Contract |
|---|---|
| Models | `/v1/models` returns an OpenAI list shape and the model can be called |
| Completion | `chat.completion` shape, one choice, assistant role, non-empty text |
| Finish reason | `max_tokens` cut is `length`, natural end is `stop` |
| Usage | `total_tokens = prompt_tokens + completion_tokens`, and completion tokens match the answer |
| Errors | 4xx and 5xx bodies are OpenAI JSON with `type`, `param`, and `code` |
| Request id | `X-Request-Id` is echoed so logs can be joined across hops |
| Stream framing | SSE content type, one finish event, `data: [DONE]` at the end |
| Stream timing | tokens arrive over time, not in one buffered write |
| Stream usage | usage is sent after finish, not before it |
| Stream deltas | each delta is new text, not the whole answer so far |

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Terminal 1: the contract-correct mock
python3 mock_server_fixed.py --port 8010

# Terminal 2: the suite
MOCK_BASE_URL=http://127.0.0.1:8010 pytest -v
```

Expected: `19 passed`.

The default mock sleeps 500 ms in prefill and 40 ms per token, so the full suite takes about 30 seconds. For a faster local run:

```bash
MOCK_PREFILL_MS=50 MOCK_DECODE_MS=20 MOCK_PREFILL_PER_TOKEN_MS=0.02 \
  python3 mock_server_fixed.py --port 8010

MOCK_BASE_URL=http://127.0.0.1:8010 pytest -v
```

Expected: `19 passed in about 6 seconds`.

## Run the fault matrix

The matrix starts the buggy server, the fixed server, and one fault proxy per mode, then runs the same 19 tests against each endpoint.

```bash
python3 run_matrix.py
```

Expected summary:

```text
fixed baseline failures: 0
buggy baseline failures: 3
...
10 of 10 fault modes were caught
```

The buggy baseline has two seeded defects but three failures because `finish_reason` is wrong in both the non-streaming and streaming paths.

## Fault modes

| Mode | What it breaks | Test that catches it |
|---|---|---|
| `drop-done` | removes `data: [DONE]` | `stream_ends_with_done` |
| `wrong-finish` | rewrites every finish reason to `stop` | `finish_reason_length`, `stream_finish_reason_matches_cut` |
| `mangle-usage` | makes `total_tokens` equal only completion tokens | `usage_is_consistent`, `stream_chunks` |
| `html-error` | replaces JSON errors with HTML | `error_shape_404`, `error_shape_bad_json`, `unknown_path_is_json_404` |
| `strip-request-id` | removes the `X-Request-Id` response header | `request_id_is_echoed` |
| `buffer` | sends the whole stream in one write | `stream_is_incremental` |
| `truncate-stream` | closes after five data lines | several streaming tests |
| `duplicate-finish` | sends the finish event twice | `stream_has_one_finish` |
| `usage-before-finish` | moves usage ahead of finish | `stream_usage_comes_after_finish` |
| `cumulative-deltas` | rewrites every delta as the whole answer so far | `stream_deltas_are_not_cumulative` |

## Test a real gateway

Point the suite at any OpenAI-compatible endpoint:

```bash
MOCK_BASE_URL=http://127.0.0.1:4000 \
MOCK_API_KEY=your-key \
pytest -v
```

If the gateway needs no key, leave `MOCK_API_KEY` unset. The default is `test`.

The suite does not retry. A failure is a finding, not a flake. Re-run once to confirm, then write down the endpoint, version, request, and response.

## LiteLLM 1.106.0 run

LiteLLM was placed in front of `mock_server_fixed.py` with a model alias:

```yaml
model_list:
  - model_name: mock-small
    litellm_params:
      model: openai/mock-small
      api_base: http://127.0.0.1:8010/v1
      api_key: test
```

```bash
LITELLM_MASTER_KEY=sk-local \
OPENAI_API_KEY=test \
litellm --config litellm.config.yaml --port 4000

MOCK_BASE_URL=http://127.0.0.1:4000 \
MOCK_API_KEY=sk-local \
pytest -v
```

Result: 15 passed, 4 failed. The failures are in [reports/002-litellm.md](reports/002-litellm.md).

## Repository layout

```text
conftest.py            fixtures for the OpenAI SDK, raw httpx, and auth
test_contract.py       19 contract tests
mock_server_fixed.py   contract-correct mock
mock_server.py         intentionally buggy mock with two seeded defects
fault_proxy.py         one fault mode at a time, standard library only
run_matrix.py          starts every scenario and prints the pass/fail matrix
reports/               run logs and findings
atlas/                 failure atlas entries
```

## Design rules

- CPU only. No GPU, no Docker, no cluster.
- The OpenAI SDK is used where real clients use it. Raw httpx is used where the SDK hides framing, headers, and error bodies.
- Each fault has one obvious catcher. If a fault is caught by everything, the test is too broad.
- The suite tests the contract, not the model. The mock has no model quality to measure.

## License

MIT
