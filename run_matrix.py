#!/usr/bin/env python3
"""
Conformance matrix runner.

Starts the buggy server, the fixed server, and one fault proxy per mode.
Runs the same pytest suite against each endpoint and prints a pass/fail
matrix, so you can see which test catches which lie.

Usage:
  python3 run_matrix.py

Requires openai, pytest and httpx in the active environment.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

SERVER_ENV = {
    "MOCK_PREFILL_MS": "50",
    "MOCK_DECODE_MS": "20",
    "MOCK_PREFILL_PER_TOKEN_MS": "0.02",
}

SCENARIOS = [
    ("fixed", 8010, None),
    ("buggy", 8020, None),
    ("drop", 8001, "drop-done"),
    ("finish", 8002, "wrong-finish"),
    ("usage", 8003, "mangle-usage"),
    ("html", 8004, "html-error"),
    ("rid", 8005, "strip-request-id"),
    ("buf", 8006, "buffer"),
    ("trunc", 8007, "truncate-stream"),
    ("dupf", 8008, "duplicate-finish"),
    ("ord", 8009, "usage-before-finish"),
    ("cum", 8011, "cumulative-deltas"),
    ("targs", 8012, "tool-escape"),
]


def wait_port(port, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def start(args, env=None):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.Popen(
        [PY] + args,
        cwd=str(ROOT),
        env=full_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_all(procs):
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()


def run_suite(port):
    env = os.environ.copy()
    env["MOCK_BASE_URL"] = "http://127.0.0.1:%d" % port
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [PY, "-m", "pytest", "test_contract.py", "-v", "--tb=no",
         "-p", "no:cacheprovider"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    outcomes = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "::" in parts[0]:
            name = parts[0].rsplit("::", 1)[-1]
            status = parts[1]
            if status in ("PASSED", "FAILED", "ERROR", "SKIPPED"):
                outcomes[name] = status
    return outcomes, result.stdout + result.stderr


def main():
    procs = []
    try:
        print("starting servers and proxies ...", flush=True)

        procs.append(start(["mock_server_fixed.py", "--port", "8010"], SERVER_ENV))
        procs.append(start(["mock_server.py", "--port", "8020"], SERVER_ENV))
        for name, port, mode in SCENARIOS:
            if mode is not None:
                procs.append(start([
                    "fault_proxy.py", "--mode", mode,
                    "--port", str(port), "--upstream", "8010",
                ]))

        for name, port, mode in SCENARIOS:
            if not wait_port(port):
                print("could not start scenario %s on port %d" % (name, port))
                return 1

        # Warm the servers so the first test does not pay import cost.
        wait_port(8010)
        wait_port(8020)

        results = {}
        for name, port, mode in SCENARIOS:
            print("running suite against %-6s on %d ..." % (name, port), flush=True)
            outcomes, raw = run_suite(port)
            if not outcomes:
                print(raw[-2000:])
            results[name] = outcomes

        all_tests = sorted({t for outcomes in results.values() for t in outcomes})
        cols = [s[0] for s in SCENARIOS]

        label_width = max([len(t) for t in all_tests] + [len("test")]) + 2
        col_width = 7

        print()
        print("OpenAI gateway conformance matrix")
        print("PASS = the contract held, FAIL = the fault was caught")
        print()
        header = "test".ljust(label_width)
        for col in cols:
            header += col.center(col_width)
        print(header)
        print("-" * len(header))

        for test in all_tests:
            short = test[len("test_"):] if test.startswith("test_") else test
            row = short.ljust(label_width)
            for name, _, _ in SCENARIOS:
                status = results[name].get(test, "MISSING")
                mark = "PASS" if status == "PASSED" else "FAIL"
                row += mark.center(col_width)
            print(row)

        print()
        fixed_fails = [t for t, s in results["fixed"].items() if s != "PASSED"]
        buggy_fails = [t for t, s in results["buggy"].items() if s != "PASSED"]

        print("fixed baseline failures: %d" % len(fixed_fails))
        for t in fixed_fails:
            print("  - " + t)
        print("buggy baseline failures: %d" % len(buggy_fails))
        for t in buggy_fails:
            print("  - " + t)

        caught = 0
        for name, _, mode in SCENARIOS:
            if mode is None:
                continue
            fails = [t for t, s in results[name].items() if s != "PASSED"]
            if fails:
                caught += 1
            print("%-7s caught by: %s" % (name, ", ".join(fails) if fails else "NOTHING"))

        total_faults = len([s for s in SCENARIOS if s[2] is not None])
        print()
        print("%d of %d fault modes were caught" % (caught, total_faults))
        return 0 if len(fixed_fails) == 0 and caught == total_faults else 1
    finally:
        stop_all(procs)


if __name__ == "__main__":
    sys.exit(main())
