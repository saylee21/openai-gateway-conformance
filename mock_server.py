#!/usr/bin/env python3
"""
Mock OpenAI server, intentionally non-conformant.

This is the "before" case. It has two seeded contract defects. Do not fix
them by hand. Run the test suite, read the failures, then compare with
mock_server_fixed.py.

  1. finish_reason is always "stop", even when max_tokens cut the answer.
  2. X-Request-Id is generated, not echoed from the incoming request.

Standard library only, except tiktoken for real counts.

Run:  python3 mock_server.py
Then: curl -s localhost:8000/v1/models
"""

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFILL_BASE_MS = int(os.environ.get("MOCK_PREFILL_MS", "500"))
PREFILL_PER_TOKEN_MS = float(os.environ.get("MOCK_PREFILL_PER_TOKEN_MS", "0.2"))
DECODE_MS = int(os.environ.get("MOCK_DECODE_MS", "40"))
MAX_TOKENS = int(os.environ.get("MOCK_MAX_TOKENS", "64"))

try:
    import tiktoken
    ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text):
        return len(ENC.encode(text))
except Exception:
    ENC = None

    def count_tokens(text):
        # Fallback estimate: about 4 characters per token.
        return max(1, len(text) // 4)


def now():
    return time.time()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args), flush=True)

    def _json(self, code, payload, extra_headers=None):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-Id", getattr(self, "request_id", ""))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, message, etype="invalid_request_error", param=None, err_code=None):
        self._json(code, {"error": {"message": message, "type": etype,
                                    "param": param, "code": err_code}})

    def do_GET(self):
        self.request_id = "req_" + uuid.uuid4().hex[:12]
        if self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [
                {"id": "mock-small", "object": "model", "created": 0, "owned_by": "local"},
            ]})
        elif self.path.startswith("/tokenize"):
            text = self.path.split("text=", 1)[1] if "text=" in self.path else ""
            from urllib.parse import unquote
            text = unquote(text)
            self._json(200, {"text": text, "chars": len(text),
                             "tokens": count_tokens(text),
                             "encoder": "cl100k_base" if ENC else "estimate"})
        else:
            self._error(404, "Unknown path " + self.path, param="path")

    def do_POST(self):
        self.request_id = "req_" + uuid.uuid4().hex[:12]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        try:
            req = json.loads(raw)
        except Exception:
            return self._error(400, "Request body is not valid JSON")

        if self.path != "/v1/chat/completions":
            return self._error(404, "Unknown path " + self.path, param="path")

        model = req.get("model")
        if model != "mock-small":
            return self._error(404, "The model '%s' does not exist" % model,
                               param="model", err_code="model_not_found")

        messages = req.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._error(400, "'messages' must be a non-empty array", param="messages")

        prompt_text = " ".join(str(m.get("content", "")) for m in messages)
        prompt_tokens = count_tokens(prompt_text)
        max_tokens = min(int(req.get("max_tokens") or 24), MAX_TOKENS)
        stream = bool(req.get("stream"))
        include_usage = bool((req.get("stream_options") or {}).get("include_usage"))

        # Pretend prefill. This is the cost of reading the prompt.
        time.sleep((PREFILL_BASE_MS + prompt_tokens * PREFILL_PER_TOKEN_MS) / 1000.0)

        words = ("Prefill read %d prompt tokens. Decode now emits one token at a time. "
                 "Each step re-reads the cache. That is why the first token is slow "
                 "and later tokens are cheap but not free." % prompt_tokens).split()
        tokens = (words * 4)[:max_tokens]

        if stream:
            self._stream(tokens, model, prompt_tokens, include_usage)
        else:
            self._complete(tokens, model, prompt_tokens)

    def _complete(self, tokens, model, prompt_tokens):
        text = " ".join(tokens)
        for _ in tokens:
            time.sleep(DECODE_MS / 1000.0)
        self._json(200, {
            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
            "object": "chat.completion",
            "created": int(now()),
            "model": model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens,
                      "completion_tokens": len(tokens),
                      "total_tokens": prompt_tokens + len(tokens)},
        })

    def _stream(self, tokens, model, prompt_tokens, include_usage):
        cid = "chatcmpl-" + uuid.uuid4().hex[:12]
        created = int(now())
        # SSE requires this content type. No Content-Length, the stream ends the body.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Request-Id", self.request_id)
        self.end_headers()
        self.close_connection = True

        def send(obj):
            self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
            self.wfile.flush()

        def chunk(delta, finish=None):
            return {"id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": model, "choices": [{"index": 0, "delta": delta,
                                                 "finish_reason": finish}]}

        try:
            send(chunk({"role": "assistant", "content": ""}))
            last = len(tokens) - 1
            for i, t in enumerate(tokens):
                time.sleep(DECODE_MS / 1000.0)
                send(chunk({"content": t + (" " if i < last else "")}))
            send(chunk({}, "stop"))
            if include_usage:
                final = {"id": cid, "object": "chat.completion.chunk",
                         "created": created, "model": model, "choices": [],
                         "usage": {"prompt_tokens": prompt_tokens,
                                   "completion_tokens": len(tokens),
                                   "total_tokens": prompt_tokens + len(tokens)}}
                send(final)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            print("[%s] client disconnected mid-stream after %s"
                  % (time.strftime("%H:%M:%S"), self.request_id), flush=True)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("buggy mock on http://127.0.0.1:%d  prefill=%dms + %.2fms/token, decode=%dms/token"
          % (args.port, PREFILL_BASE_MS, PREFILL_PER_TOKEN_MS, DECODE_MS), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
