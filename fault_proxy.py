#!/usr/bin/env python3
"""
Fault-injection proxy. Standard library only.

It sits between the client and a correct mock server and breaks one promise
at a time. The conformance suite should catch every mode.

Usage:
  python3 fault_proxy.py --mode drop-done --port 8001 --upstream 8010

Modes:
  drop-done        remove the data: [DONE] sentinel
  wrong-finish     rewrite every finish_reason to "stop"
  mangle-usage     make total_tokens equal completion_tokens
  html-error       replace 4xx and 5xx JSON bodies with HTML
  strip-request-id remove the X-Request-Id response header
  buffer           hold the whole stream, then send it at once
  truncate-stream  forward five data lines, then close without DONE
  duplicate-finish send the finish_reason event twice
  usage-before-finish move the usage chunk ahead of finish_reason
  cumulative-deltas rewrite every content delta as the whole answer so far

The proxy only rewrites the response. It never touches the request body.
"""

import argparse
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODES = [
    "drop-done",
    "wrong-finish",
    "mangle-usage",
    "html-error",
    "strip-request-id",
    "buffer",
    "truncate-stream",
    "duplicate-finish",
    "usage-before-finish",
    "cumulative-deltas",
]

UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 8010
MODE = "drop-done"
TRUNCATE_AFTER = 5


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None

        headers = {}
        for key, value in self.headers.items():
            if key.lower() in ("host", "content-length", "connection"):
                continue
            headers[key] = value
        if body is not None:
            headers["Content-Length"] = str(len(body))

        conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=60)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            content_type = resp.getheader("Content-Type", "") or ""
            if "text/event-stream" in content_type:
                self._relay_stream(resp)
            else:
                self._relay_json(resp)
        finally:
            conn.close()

    def _send_headers(self, resp, content_length=None, content_type=None):
        self.send_response(resp.status)
        for key, value in resp.getheaders():
            low = key.lower()
            if low in ("content-length", "connection", "transfer-encoding"):
                continue
            if low == "content-type" and content_type is not None:
                continue
            if MODE == "strip-request-id" and low == "x-request-id":
                continue
            self.send_header(key, value)
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _relay_json(self, resp):
        data = resp.read()

        if MODE == "html-error" and resp.status >= 400:
            payload = (b"<html><body><h1>502 Bad Gateway</h1>"
                       b"<p>upstream returned an error</p></body></html>")
            self._send_headers(resp, len(payload), "text/html")
            self.wfile.write(payload)
            return

        if MODE == "wrong-finish":
            data = self._rewrite_finish(data)

        if MODE == "mangle-usage":
            data = self._mangle_usage(data)

        self._send_headers(resp, len(data))
        self.wfile.write(data)

    def _rewrite_finish(self, data):
        try:
            obj = json.loads(data)
        except Exception:
            return data
        for choice in obj.get("choices") or []:
            if choice.get("finish_reason") is not None:
                choice["finish_reason"] = "stop"
        return json.dumps(obj).encode()

    def _mangle_usage(self, data):
        try:
            obj = json.loads(data)
        except Exception:
            return data
        usage = obj.get("usage")
        if isinstance(usage, dict):
            usage["total_tokens"] = usage.get("completion_tokens", 0)
        return json.dumps(obj).encode()

    @staticmethod
    def _event(line):
        if not line.startswith(b"data: {"):
            return None
        try:
            return json.loads(line[len(b"data: "):])
        except Exception:
            return None

    @classmethod
    def _has_finish(cls, line):
        obj = cls._event(line)
        return bool(obj and any(c.get("finish_reason")
                                for c in (obj.get("choices") or [])))

    @classmethod
    def _has_usage(cls, line):
        obj = cls._event(line)
        return bool(obj and obj.get("usage"))

    @classmethod
    def _cumulative_line(cls, line, so_far):
        obj = cls._event(line)
        if obj is None:
            return line, so_far
        changed = False
        for choice in obj.get("choices") or []:
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if text:
                so_far += text
                delta["content"] = so_far
                changed = True
        if not changed:
            return line, so_far
        return b"data: " + json.dumps(obj).encode(), so_far

    def _relay_stream(self, resp):
        self._send_headers(resp)

        if MODE == "buffer":
            data = resp.read()
            self.wfile.write(data)
            self.wfile.flush()
            return

        data_lines = 0
        held_finish = None
        skip_blank = False
        cumulative = ""

        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.rstrip(b"\r\n")
            if not line:
                if skip_blank:
                    skip_blank = False
                    continue
                self.wfile.write(b"\n")
                self.wfile.flush()
                continue

            if MODE == "truncate-stream" and data_lines >= TRUNCATE_AFTER:
                self.wfile.flush()
                return

            if MODE == "drop-done" and line == b"data: [DONE]":
                continue

            if MODE == "usage-before-finish":
                if self._has_finish(line):
                    held_finish = line
                    skip_blank = True
                    continue
                if self._has_usage(line) and held_finish is not None:
                    self.wfile.write(line + b"\n\n")
                    self.wfile.write(held_finish + b"\n\n")
                    self.wfile.flush()
                    held_finish = None
                    skip_blank = True
                    data_lines += 2
                    continue
                if line == b"data: [DONE]" and held_finish is not None:
                    self.wfile.write(held_finish + b"\n\n")
                    self.wfile.flush()
                    held_finish = None

            if MODE == "wrong-finish":
                line = self._rewrite_finish_line(line)

            if MODE == "mangle-usage":
                line = self._mangle_usage_line(line)

            if MODE == "cumulative-deltas":
                line, cumulative = self._cumulative_line(line, cumulative)

            self.wfile.write(line + b"\n")
            self.wfile.flush()
            data_lines += 1

            if MODE == "duplicate-finish" and self._has_finish(line):
                self.wfile.write(line + b"\n")
                self.wfile.flush()

    def _rewrite_finish_line(self, line):
        if not line.startswith(b"data: {"):
            return line
        try:
            obj = json.loads(line[len(b"data: "):])
        except Exception:
            return line
        for choice in obj.get("choices") or []:
            if choice.get("finish_reason") is not None:
                choice["finish_reason"] = "stop"
        return b"data: " + json.dumps(obj).encode()

    def _mangle_usage_line(self, line):
        if not line.startswith(b"data: {"):
            return line
        try:
            obj = json.loads(line[len(b"data: "):])
        except Exception:
            return line
        usage = obj.get("usage")
        if isinstance(usage, dict):
            usage["total_tokens"] = usage.get("completion_tokens", 0)
        return b"data: " + json.dumps(obj).encode()


def main():
    global UPSTREAM_PORT, MODE
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--upstream", type=int, default=8010)
    args = parser.parse_args()

    MODE = args.mode
    UPSTREAM_PORT = args.upstream

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), ProxyHandler)
    print("fault proxy mode=%s on http://127.0.0.1:%d -> 127.0.0.1:%d"
          % (MODE, args.port, UPSTREAM_PORT), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
