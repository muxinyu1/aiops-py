#!/usr/bin/env python3
"""End-to-end demo: start the example microservice under the OTel Java agent
*and* JaCoCo, fire a few HTTP requests, and print execution traces with
statement-level covered lines.

Run from the project root::

    python demo_trace.py
"""

import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from executor import JavaExecutor
from jacoco_client import download_jacoco
from parameter import HttpParameter

JAR = os.path.join(
    REPO, "examples", "java-microservice",
    "target", "java-microservice-0.0.1-SNAPSHOT.jar",
)
AGENT       = os.path.join(REPO, "opentelemetry-javaagent.jar")
CLASSFILES  = os.path.join(REPO, "examples", "java-microservice", "target", "classes")
SOURCE_DIR  = os.path.join(REPO, "examples", "java-microservice", "src", "main", "java")
BASE_URL    = "http://localhost:8080"


def print_trace(trace) -> None:
    if not trace.nodes:
        print("  (no instrumented spans captured)")
        return
    print(f"  {len(trace.nodes)} node(s):")
    for i, node in enumerate(trace.nodes):
        loc = f"{node.src_file}:{node.line_number}" if node.line_number else node.src_file
        dur = f"{node.duration_ns / 1_000_000:.2f} ms"
        err = "  ← ERROR" if node.is_error else ""
        print(f"  [{i:2d}]  {node.content:<45}  {loc:<30}  {dur}{err}")
        print(f"        sig={node.method_signature}  ns={node.class_namespace}")
        if node.executed_lines:
            lines_str = ", ".join(str(ln) for ln in node.executed_lines)
            print(f"        executed lines: [{lines_str}]")


def main() -> None:
    print("=" * 65)
    print("  AIOps directed-fuzz — Java statement-level tracing demo")
    print("=" * 65)
    print(f"  JAR  : {JAR}")
    print(f"  Agent: {AGENT}")
    print()

    # Auto-download JaCoCo JARs from Maven Central if not present
    print("Ensuring JaCoCo JARs are present…")
    jacoco_agent, jacoco_cli = download_jacoco(dest_dir=REPO)
    print(f"  agent : {jacoco_agent}")
    print(f"  cli   : {jacoco_cli}")
    print()

    ex = JavaExecutor(
        jar_path=JAR,
        agent_path=AGENT,
        base_url=BASE_URL,
        jacoco_agent_path=jacoco_agent,
        jacococli_jar_path=jacoco_cli,
        jacoco_classfiles=CLASSFILES,
        jacoco_source_dir=SOURCE_DIR,
    )

    print("Starting Java microservice with OTel + JaCoCo agents (may take ~25 s)…")
    ex.start()
    print("Microservice ready.\n")

    # ── test cases ────────────────────────────────────────────────────────
    tests: list[tuple[str, HttpParameter]] = [
        (
            "GET /api/users/1  → happy path",
            HttpParameter("GET", f"{BASE_URL}/api/users/1"),
        ),
        (
            "GET /api/users/999 → user not found (warn path)",
            HttpParameter("GET", f"{BASE_URL}/api/users/999"),
        ),
        (
            "POST /api/users   → create user",
            HttpParameter(
                "POST", f"{BASE_URL}/api/users",
                body='{"name":"Charlie","email":"charlie@example.com"}',
            ),
        ),
        (
            "POST /api/users   → bad email (error path)",
            HttpParameter(
                "POST", f"{BASE_URL}/api/users",
                body='{"name":"Dave","email":"not-an-email"}',
            ),
        ),
        (
            "GET /api/orders/1 → order lookup",
            HttpParameter("GET", f"{BASE_URL}/api/orders/1"),
        ),
    ]

    for label, param in tests:
        print(f">>> {label}")
        trace = ex.execute(param)
        print_trace(trace)
        print()

    print("Stopping microservice.")
    ex.stop()
    print("Done.")


if __name__ == "__main__":
    main()
