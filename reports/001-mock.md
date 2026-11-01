# Report 001: contract-correct mock

Run date: 2026-11-01

## Environment

- macOS, arm64
- Python 3.14.4
- `openai` 3.42.0
- `pytest` 9.2.0
- `httpx` 0.28.1
- `tiktoken` 0.14.0
- No GPU, no Docker

## Command

```bash
MOCK_PREFILL_MS=50 \
MOCK_DECODE_MS=20 \
MOCK_PREFILL_PER_TOKEN_MS=0.02 \
python3 mock_server_fixed.py --port 8010
```

```bash
MOCK_BASE_URL=http://127.0.0.1:8010 pytest -v
```

## Result

```text
collected 19 items

test_contract.py::test_models_list PASSED
test_contract.py::test_models_list_raw PASSED
test_contract.py::test_completion_shape PASSED
test_contract.py::test_usage_is_consistent PASSED
test_contract.py::test_finish_reason_length PASSED
test_contract.py::test_finish_reason_stop PASSED
test_contract.py::test_ids_are_unique PASSED
test_contract.py::test_error_shape_404 PASSED
test_contract.py::test_error_shape_bad_json PASSED
test_contract.py::test_unknown_path_is_json_404 PASSED
test_contract.py::test_request_id_is_echoed PASSED
test_contract.py::test_stream_chunks PASSED
test_contract.py::test_stream_ends_with_done PASSED
test_contract.py::test_stream_is_incremental PASSED
test_contract.py::test_stream_finish_reason_matches_cut PASSED
test_contract.py::test_stream_has_one_finish PASSED
test_contract.py::test_stream_usage_comes_after_finish PASSED
test_contract.py::test_stream_deltas_are_not_cumulative PASSED
test_contract.py::test_sdk_raises_typed_error PASSED

19 passed in 5.88s
```

## Fault matrix

Command:

```bash
python3 run_matrix.py
```

The runner starts the fixed server, the buggy server, and ten proxies, then runs the same 19 tests against each endpoint.

```text
fixed baseline failures: 0
buggy baseline failures: 3
  - test_finish_reason_length
  - test_request_id_is_echoed
  - test_stream_finish_reason_matches_cut

drop    caught by: test_stream_ends_with_done
finish  caught by: test_finish_reason_length, test_stream_finish_reason_matches_cut
usage   caught by: test_usage_is_consistent, test_stream_chunks
html    caught by: test_error_shape_404, test_error_shape_bad_json, test_unknown_path_is_json_404
rid     caught by: test_request_id_is_echoed
buf     caught by: test_stream_is_incremental
trunc   caught by: test_stream_chunks, test_stream_ends_with_done, test_stream_is_incremental,
                   test_stream_finish_reason_matches_cut, test_stream_has_one_finish,
                   test_stream_usage_comes_after_finish, test_stream_deltas_are_not_cumulative
dupf    caught by: test_stream_chunks, test_stream_finish_reason_matches_cut, test_stream_has_one_finish
ord     caught by: test_stream_usage_comes_after_finish
cum     caught by: test_stream_deltas_are_not_cumulative

10 of 10 fault modes were caught
```

## What this proves

The suite is not vacuous. It passes a server that keeps the contract and fails a server that breaks it.

The buggy baseline has two seeded defects: `finish_reason` is always `stop`, and `X-Request-Id` is generated instead of echoed. It produces three failures because `finish_reason` is checked in both the non-streaming and streaming paths.

The new streaming tests are isolated:

- `duplicate-finish` is caught only by the finish-count and stream-shape tests.
- `usage-before-finish` is caught only by `test_stream_usage_comes_after_finish`.
- `cumulative-deltas` is caught only by `test_stream_deltas_are_not_cumulative`.

The `truncate-stream` mode is intentionally broad. A stream that dies mid-answer should trip almost every streaming promise at once, and it does.

## Limit

This report says the suite is internally consistent. It does not say a real gateway is conformant. Report 002 is the first real gateway run.
