# Report 004: tool-call arguments split after a backslash

Run date: 2026-11-06

## Why this report

Huilong (@098uyiubgvgcss) replied to a public thread about the suite with a
concrete edge case: a tool-call fixture whose argument JSON is split right
after a backslash, and a check on the reconstructed arguments, not only on
whether every SSE frame parses.

That is a real bug class. This report records the fixture, the test, the
result on the contract-correct mock, and the result through LiteLLM 1.108.0.

## What the contract promises

A streamed tool call carries
`delta.tool_calls[].function.arguments` as a string. The fragments
concatenate into one JSON string. The client parses that string.

A gateway may split or merge fragments. It must not change the bytes of the
reconstructed string.

The hard boundary is a split inside a JSON escape. The first fragment ends
with a single backslash. That fragment is not valid JSON on its own. A
gateway that parses each fragment, trims it, or re-encodes it will corrupt
the arguments.

## Fixture

The arguments string, before it is split, is this JSON source:

```json
{"path": "C:\\Users\\saylee\\notes.txt", "pattern": "\\d+\\s*"}
```

The mock splits it after the first backslash in `C:\\Users`. The two
fragments, as the client sees them after SSE decoding, are:

```text
{"path": "C:\
\Users\\saylee\\notes.txt", "pattern": "\\d+\\s*"}
```

Fragment one is not valid JSON. Fragment two is not valid JSON. Their
concatenation is valid JSON, and it parses to:

```python
{"path": "C:\\Users\\saylee\\notes.txt", "pattern": "\\d+\\s*"}
```

In Python terms, the parsed path is `C:\Users\saylee\notes.txt` and the
parsed pattern is `\d+\s*`.

## Test

`test_stream_tool_call_arguments_split_after_backslash` in
`test_contract.py`.

It sends one streaming request with a `write_file` tool definition, collects
every `arguments` fragment, concatenates them, parses the result, and
compares the parsed object to the expected object.

It deliberately does not compare raw bytes. JSON key order is not part of
the contract. A gateway that reorders keys is not breaking a client. A
gateway that changes a backslash is.

## Result: contract-correct mock

```text
20 passed in 17.48s
```

## Result: LiteLLM 1.108.0

```text
16 passed, 4 failed in 18.41s
```

The four failures are the same four recorded in Reports 002 and 003:
`test_error_shape_404`, `test_unknown_path_is_json_404`,
`test_request_id_is_echoed`, and `test_sdk_raises_typed_error`.

The tool-call test passed. LiteLLM 1.108.0 preserves this boundary on this
fixture.

## Proof that the test can fail

`fault_proxy.py --mode tool-escape` doubles every backslash in each
argument fragment.

```bash
python3 fault_proxy.py --mode tool-escape --port 8012 --upstream 8010
MOCK_BASE_URL=http://127.0.0.1:8012 pytest -q \
  test_contract.py::test_stream_tool_call_arguments_split_after_backslash
```

The test fails with:

```text
{'path': 'C:\\\\Users\\\\saylee\\\\notes.txt'} != {'path': 'C:\\Users\\saylee\\notes.txt'}
{'pattern': '\\\\d+\\\\s*'} != {'pattern': '\\d+\\s*'}
```

That is the point. Without this mode, a passing test would only show that
the mock agrees with itself.

## What this does not prove

- It does not prove that a gateway handles every split point, every escape,
  or every tool schema.
- It does not prove that LiteLLM is correct in general. It proves one
  fixture on one version.
- It does not check model behavior. The arguments are a fixed string, so the
  mock has no model quality to measure.
- It does not check key order, because key order is not part of the contract.

## Reproduce

```bash
# Terminal 1
python3 mock_server_fixed.py --port 8010

# Terminal 2
MOCK_BASE_URL=http://127.0.0.1:8010 pytest -q

# Optional: the fault mode
python3 fault_proxy.py --mode tool-escape --port 8012 --upstream 8010
MOCK_BASE_URL=http://127.0.0.1:8012 pytest -q \
  test_contract.py::test_stream_tool_call_arguments_split_after_backslash
```

## Status

No upstream issue was opened. LiteLLM passes this test, so there is nothing
to report to LiteLLM. The test stays in the suite as a regression guard for
the next gateway, and the `tool-escape` mode proves it can fail.
