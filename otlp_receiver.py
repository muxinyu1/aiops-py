"""Minimal OTLP/HTTP span receiver supporting both protobuf and JSON.

The OpenTelemetry Java agent 2.x exports spans via HTTP POST to
    http://localhost:{port}/v1/traces
with Content-Type: application/x-protobuf  (default, ``http/protobuf`` protocol)
                or application/json         (``http/json`` protocol, not in 2.x agent)

This module provides OtlpReceiver which:
  - Runs a background HTTP server to accept those POSTs
  - Parses both protobuf binary and JSON payloads
  - Indexes spans by trace-id (normalised to lowercase hex)
  - Exposes collect(trace_id) to retrieve all spans for a given request
"""

from __future__ import annotations

import base64
import json
import re
import struct
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def _to_hex(raw: str) -> str:
    """Normalise an OTLP trace/span ID to lowercase hex.

    OTel OTLP JSON may encode IDs as lowercase hex or standard base64.
    Protobuf gives raw bytes which are already hex-ed at parse time.
    """
    if _HEX_RE.match(raw):
        return raw.lower()
    try:
        padding = (4 - len(raw) % 4) % 4
        decoded = base64.b64decode(raw + "=" * padding)
        return decoded.hex()
    except Exception:
        return raw.lower()


# ── Minimal protobuf binary decoder ─────────────────────────────────────────
# Only the fields needed for OTLP ExportTraceServiceRequest are extracted.
# Reference: opentelemetry-proto/opentelemetry/proto/trace/v1/trace.proto

def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while pos < len(buf):
        byte = buf[pos]; pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
    return result, pos


def _parse_fields(buf: bytes) -> dict[int, list]:
    """Decode a protobuf message body into {field_number: [values]}.

    Values are:
      - int for wire-type 0 (varint) and wire-type 1/5 (fixed)
      - bytes for wire-type 2 (length-delimited: strings, embedded messages)
    """
    fields: dict[int, list] = {}
    pos = 0
    while pos < len(buf):
        try:
            tag, pos = _read_varint(buf, pos)
        except IndexError:
            break
        field_num = tag >> 3
        wire_type = tag & 0x7
        try:
            if wire_type == 0:        # varint
                val, pos = _read_varint(buf, pos)
            elif wire_type == 1:      # 64-bit fixed (fixed64 / sfixed64 / double)
                val = struct.unpack_from("<Q", buf, pos)[0]
                pos += 8
            elif wire_type == 2:      # length-delimited
                length, pos = _read_varint(buf, pos)
                val = buf[pos: pos + length]
                pos += length
            elif wire_type == 5:      # 32-bit fixed (fixed32 / sfixed32 / float)
                val = struct.unpack_from("<I", buf, pos)[0]
                pos += 4
            else:
                break                 # unknown wire type — stop gracefully
        except (struct.error, IndexError):
            break
        fields.setdefault(field_num, []).append(val)
    return fields


def _str(buf: bytes | int) -> str:
    return buf.decode("utf-8", errors="replace") if isinstance(buf, bytes) else str(buf)


def _parse_any_value(buf: bytes) -> Any:
    """Decode an AnyValue message and return the Python value."""
    f = _parse_fields(buf)
    if 1 in f:   return _str(f[1][0])         # string_value
    if 3 in f:   return f[3][0]                # int_value (varint → int)
    if 4 in f:   return struct.unpack("<d", struct.pack("<Q", f[4][0]))[0]  # double_value
    if 2 in f:   return bool(f[2][0])          # bool_value
    return None


def _parse_span(buf: bytes) -> dict[str, Any]:
    """Decode a Span message into a normalised dict compatible with the span-to-node helpers."""
    f = _parse_fields(buf)

    # Binary IDs → lowercase hex strings
    trace_id    = f[1][0].hex() if 1 in f and isinstance(f[1][0], bytes) else ""
    span_id     = f[2][0].hex() if 2 in f and isinstance(f[2][0], bytes) else ""
    parent_id   = f[4][0].hex() if 4 in f and isinstance(f[4][0], bytes) else ""
    name        = _str(f[5][0])  if 5 in f else ""
    start_ns    = f[7][0]        if 7 in f else 0   # fixed64
    end_ns      = f[8][0]        if 8 in f else 0

    # Decode repeated KeyValue attributes (field 9)
    attributes: list[dict[str, Any]] = []
    for kv_buf in f.get(9, []):
        kv = _parse_fields(kv_buf)
        key = _str(kv[1][0]) if 1 in kv else ""
        val = _parse_any_value(kv[2][0]) if 2 in kv and isinstance(kv[2][0], bytes) else None
        attributes.append({"key": key, "value": {"stringValue": val} if isinstance(val, str)
                           else {"intValue": val} if isinstance(val, int) else {}})

    # Span.status = field 15 (embedded Status message)
    # Status.code = field 3 (enum: 0=UNSET, 1=OK, 2=ERROR)
    status_buf = f.get(15, [None])[0]
    is_error = False
    if isinstance(status_buf, bytes):
        sf = _parse_fields(status_buf)
        is_error = sf.get(3, [0])[0] == 2  # StatusCode.ERROR

    return {
        "traceId":           trace_id,
        "spanId":            span_id,
        "parentSpanId":      parent_id,
        "name":              name,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano":   str(end_ns),
        "attributes":        attributes,
        "isError":           is_error,
    }


def _parse_otlp_protobuf(body: bytes) -> list[dict[str, Any]]:
    """Decode ExportTraceServiceRequest → flat list of span dicts."""
    spans: list[dict] = []
    req = _parse_fields(body)
    for rs_buf in req.get(1, []):              # repeated ResourceSpans
        rs = _parse_fields(rs_buf)
        for ss_buf in rs.get(2, []):           # repeated ScopeSpans
            ss = _parse_fields(ss_buf)
            for span_buf in ss.get(2, []):     # repeated Span
                spans.append(_parse_span(span_buf))
    return spans


def _parse_otlp_json(body: bytes) -> list[dict[str, Any]]:
    """Decode OTLP/JSON ExportTraceServiceRequest → flat list of span dicts."""
    spans: list[dict] = []
    try:
        data = json.loads(body)
        for rs in data.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                spans.extend(ss.get("spans", []))
    except Exception:
        pass
    return spans


class OtlpReceiver:
    """Thread-safe background HTTP server that collects OTLP/HTTP JSON spans."""

    def __init__(self, host: str = "localhost", port: int = 4318) -> None:
        # trace_id (hex) → list of raw span dicts
        self._spans: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._lock = threading.Lock()
        self._server = HTTPServer((host, port), self._make_handler())
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()

    def collect(
        self,
        trace_id: str,
        *,
        timeout: float = 5.0,
        settle: float = 0.3,
    ) -> list[dict[str, Any]]:
        """Wait for spans tagged with *trace_id* (lowercase hex) to arrive.

        Polls until the span count is stable for one settle window, or until
        *timeout* seconds elapse.  Returns the full list of raw OTLP span dicts.
        """
        key = trace_id.lower()
        deadline = time.monotonic() + timeout
        last_count = -1
        while time.monotonic() < deadline:
            with self._lock:
                count = len(self._spans.get(key, []))
            if count > 0 and count == last_count:
                break
            last_count = count
            time.sleep(0.05)
        # one extra settle window for stragglers
        time.sleep(settle)
        with self._lock:
            return list(self._spans.get(key, []))

    def _make_handler(self) -> type:
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # type: ignore[override]
                if self.path != "/v1/traces":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                content_type = self.headers.get("Content-Type", "")
                try:
                    if "json" in content_type:
                        spans = _parse_otlp_json(body)
                    else:
                        # application/x-protobuf (default for OTel Java agent 2.x)
                        spans = _parse_otlp_protobuf(body)
                    with receiver._lock:
                        for span in spans:
                            raw_id = span.get("traceId", "")
                            if raw_id:
                                hex_id = _to_hex(raw_id)
                                receiver._spans[hex_id].append(span)
                except Exception:
                    pass  # malformed payload — ignore
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.end_headers()
                self.wfile.write(b"")

            def log_message(self, fmt: str, *args: Any) -> None:
                pass  # suppress HTTP access logs

        return _Handler
