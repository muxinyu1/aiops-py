package org.dromara.auth.tracing;

public class SpanRecord {
    public String span_id;
    public String parent_span_id;
    public String trace_id;
    public String content;
    public String function;
    public String method_signature;
    public String class_namespace;
    public String src_file;
    public int    line_number;
    public long   start_ns;
    public long   duration_ns;
    public boolean is_error;
    public String  error_message;
}
