package com.example.microservice.tracing;

/**
 * Serialisable record of a single method-level span captured by TracingAspect.
 * Field names are intentionally lower_snake to match the Python TraceNode schema.
 */
public class SpanRecord {

    /** Hex span ID of this span (16 chars). */
    public String span_id;

    /** Hex span ID of the parent span (16 chars), empty string for root. */
    public String parent_span_id;

    /** Hex trace ID shared by all spans in this request (32 chars). */
    public String trace_id;

    // ── code location ────────────────────────────────────────────────────────

    /** Span name, e.g. "AppController.getUser". */
    public String content;

    /** Method name, e.g. "getUser". */
    public String function;

    /** Human-readable method signature, e.g. "getUser(Long)". */
    public String method_signature;

    /** Fully-qualified class name. */
    public String class_namespace;

    /** Source file name, e.g. "AppController.java". */
    public String src_file;

    /** First line of the method body (from bytecode LineNumberTable), or -1. */
    public int line_number;

    // ── timing ───────────────────────────────────────────────────────────────

    /** Unix epoch timestamp at span start, in nanoseconds. */
    public long start_ns;

    /** Method wall-clock duration, in nanoseconds. */
    public long duration_ns;

    // ── status ───────────────────────────────────────────────────────────────

    public boolean is_error;

    /** Exception message if is_error == true, otherwise null. */
    public String error_message;

    // ── 运行时变量快照 (由 trace-agent 在 snapshot 模式下填充) ────────────────

    /** 方法参数值的 JSON 快照, e.g. {"0":"hello","1":"42"} */
    public String args_snapshot;

    /** 返回值的 JSON 快照, e.g. {"type":"String","value":"ok"} */
    public String return_snapshot;

    /** this 对象字段的 JSON 快照, e.g. {"_class":"UserService","field":"value"} */
    public String this_snapshot;
}
