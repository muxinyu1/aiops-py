from __future__ import annotations

import secrets
import subprocess
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from otlp_receiver import OtlpReceiver
from jacoco_client import JacocoTracer
from parameter import HttpParameter, Parameter
from sink import Sink
from source import RESTfulSource, Source, Type
from trace import Trace, TraceNode


@dataclass
class Executor():
    # executor无状态
    

    def execute(self, param: Parameter) -> Trace:
        # 
        return NotImplemented


@dataclass
class JavaExecutor:
    """Runs a Spring Boot JAR under the OpenTelemetry Java agent and captures
    the method-level execution path for each HTTP request.

    Usage::

        ex = JavaExecutor(
            jar_path="examples/java-microservice/target/...jar",
            agent_path="opentelemetry-javaagent.jar",
        )
        ex.start()                      # starts receiver + Java process
        trace = ex.execute(HttpParameter("GET", "http://localhost:8080/api/users/1"))
        ex.stop()
    """

    jar_path: str
    agent_path: str
    base_url: str = "http://localhost:8080"
    otlp_port: int = 4318
    # ── optional JaCoCo statement-level coverage ──────────────────────────────
    jacoco_agent_path: str | None = None    # path to jacocoagent.jar
    jacococli_jar_path: str | None = None   # path to jacococli.jar
    jacoco_port: int = 6300
    jacoco_classfiles: str = ""             # target/classes dir
    jacoco_source_dir: str = ""             # src/main/java dir (optional)
    _process: subprocess.Popen | None = field(default=None, init=False, repr=False)
    _receiver: OtlpReceiver | None = field(default=None, init=False, repr=False)
    _jacoco: JacocoTracer | None = field(default=None, init=False, repr=False)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the OTLP receiver then launch the Java process; block until healthy."""
        self._receiver = OtlpReceiver(port=self.otlp_port)
        self._receiver.start()

        cmd = [
            "java",
            f"-javaagent:{self.agent_path}",
        ]
        # Attach JaCoCo agent when configured
        if self.jacoco_agent_path and self.jacococli_jar_path:
            jacoco_args = (
                f"output=tcpserver,port={self.jacoco_port}"
                ",includes=com.example.microservice.**"
            )
            cmd.append(f"-javaagent:{self.jacoco_agent_path}={jacoco_args}")
        cmd += [
            "-Dotel.service.name=java-microservice",
            # export traces as OTLP HTTP/protobuf to our local receiver
            "-Dotel.traces.exporter=otlp",
            f"-Dotel.exporter.otlp.endpoint=http://localhost:{self.otlp_port}",
            "-Dotel.exporter.otlp.protocol=http/protobuf",
            # reduce batch export delay so spans arrive promptly
            "-Dotel.bsp.schedule.delay=200",
            # disable unused signal exporters
            "-Dotel.metrics.exporter=none",
            "-Dotel.logs.exporter=none",
            "-jar", self.jar_path,
        ]
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_ready()
        # Initialise JaCoCo tracer *after* Spring is ready (classes loaded)
        if self.jacoco_agent_path and self.jacococli_jar_path:
            self._jacoco = JacocoTracer(
                tcp_port=self.jacoco_port,
                classfiles_dir=self.jacoco_classfiles,
                source_dir=self.jacoco_source_dir,
                jacococli_jar=self.jacococli_jar_path,
            )

    def stop(self) -> None:
        """Terminate the Java process and shut down the OTLP receiver."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._receiver:
            self._receiver.stop()

    def _wait_ready(self, timeout: float = 60.0) -> None:
        health_url = f"{self.base_url}/actuator/health"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(health_url, timeout=2) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                pass
            time.sleep(1.0)
        raise RuntimeError(
            f"Microservice at {self.base_url} did not become healthy within {timeout}s"
        )

    # ── execution ────────────────────────────────────────────────────────────

    def execute(self, param: Parameter) -> Trace:
        """Fire an HTTP request and return the resulting call-path trace.

        Injects a W3C ``traceparent`` header so that all spans produced by the
        microservice share a known trace-id.  After the response is received the
        OTLP receiver is polled until all spans for that trace-id have arrived.
        """
        if not isinstance(param, HttpParameter):
            raise TypeError(f"Expected HttpParameter, got {type(param).__name__}")

        # JaCoCo: reset counters before this request
        if self._jacoco:
            self._jacoco.reset()

        # Fresh trace-id in lowercase hex (32 chars = 16 bytes)
        trace_id = secrets.token_hex(16)
        parent_span_id = secrets.token_hex(8)     # 16-char hex
        traceparent = f"00-{trace_id}-{parent_span_id}-01"

        headers = {**param.headers, "traceparent": traceparent}
        if param.body is not None:
            headers.setdefault("Content-Type", "application/json")

        req = urllib.request.Request(
            url=param.url,
            method=param.method,
            headers=headers,
            data=param.body.encode() if param.body else None,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.HTTPError:
            pass  # non-2xx responses are valid; we still want the trace

        # Collect all spans that share our trace-id
        raw_spans = self._receiver.collect(trace_id)
        nodes = _spans_to_nodes(raw_spans)

        # JaCoCo: dump coverage and assign executed lines to each node
        if self._jacoco:
            covered = self._jacoco.dump()
            _attach_covered_lines(nodes, covered)

        return Trace(
            source=Source(type=Type.RESTFUL, data=RESTfulSource()),
            sink=Sink(),
            nodes=nodes,
        )


# ── span helpers ─────────────────────────────────────────────────────────────


def _attr_value(value_dict: dict[str, Any]) -> Any:
    """Extract the typed value from an OTLP attribute value dict."""
    for key in ("stringValue", "intValue", "boolValue", "doubleValue"):
        if key in value_dict:
            return value_dict[key]
    return None


def _spans_to_nodes(raw_spans: list[dict[str, Any]]) -> list[TraceNode]:
    """Convert a flat list of OTLP spans into a DFS pre-order TraceNode list.

    Only spans emitted by TracingAspect (those carrying ``code.namespace``) are
    kept; the framework-level HTTP root span from Spring MVC is skipped.
    The call tree is reconstructed from parent/child span IDs and traversed in
    DFS pre-order (call order), yielding the execution path as a flat list.
    """
    if not raw_spans:
        return []

    # Keep only spans from our own instrumentation
    app_spans: list[dict[str, Any]] = []
    for span in raw_spans:
        attrs: dict[str, Any] = {
            a["key"]: _attr_value(a["value"])
            for a in span.get("attributes", [])
        }
        if "code.namespace" in attrs:
            span = {**span, "_attrs": attrs}
            app_spans.append(span)

    if not app_spans:
        return []

    by_id: dict[str, dict] = {s["spanId"]: s for s in app_spans}
    children: dict[str, list[dict]] = defaultdict(list)
    roots: list[dict] = []

    for span in app_spans:
        pid = span.get("parentSpanId", "")
        if pid and pid in by_id:
            children[pid].append(span)
        else:
            roots.append(span)

    # Sort siblings by start time so call order is preserved
    for kids in children.values():
        kids.sort(key=lambda s: int(s.get("startTimeUnixNano", 0)))
    roots.sort(key=lambda s: int(s.get("startTimeUnixNano", 0)))

    result: list[TraceNode] = []

    def dfs(span: dict) -> None:
        attrs = span.get("_attrs", {})
        lineno_raw = attrs.get("code.lineno", 0)
        start  = int(span.get("startTimeUnixNano", 0))
        end    = int(span.get("endTimeUnixNano",   0))
        result.append(TraceNode(
            # source location
            src_file         = attrs.get("code.filepath", ""),
            line_number      = int(lineno_raw) if lineno_raw else 0,
            # identity
            content          = span.get("name", ""),
            function         = attrs.get("code.function", ""),
            method_signature = attrs.get("code.signature", ""),
            class_namespace  = attrs.get("code.namespace", ""),
            # call-tree links
            span_id          = span.get("spanId", ""),
            parent_span_id   = span.get("parentSpanId", ""),
            # timing
            start_ns         = start,
            duration_ns      = max(0, end - start),
            # status
            is_error         = span.get("isError", False),
        ))
        for child in children.get(span["spanId"], []):
            dfs(child)

    for root in roots:
        dfs(root)

    return result


def _attach_covered_lines(
    nodes: list[TraceNode],
    covered: dict[str, list[int]],
) -> None:
    """Assign JaCoCo-covered source lines to each TraceNode.

    Lines are bucketed by method: the lines that fall within
    [node.line_number, next_same_class_node.line_number - 1] are considered
    to belong to that node's method call.
    """
    # Group (index, node) pairs by class
    by_class: dict[str, list[tuple[int, TraceNode]]] = {}
    for i, node in enumerate(nodes):
        by_class.setdefault(node.class_namespace, []).append((i, node))

    for cls, idx_nodes in by_class.items():
        cls_lines = covered.get(cls, [])
        if not cls_lines:
            continue
        # Sort by method start line so we can compute ranges
        idx_nodes.sort(key=lambda x: x[1].line_number)
        max_line = max(cls_lines)
        for j, (idx, node) in enumerate(idx_nodes):
            start = node.line_number
            end   = idx_nodes[j + 1][1].line_number - 1 if j + 1 < len(idx_nodes) else max_line
            nodes[idx].executed_lines = [ln for ln in cls_lines if start <= ln <= end]
