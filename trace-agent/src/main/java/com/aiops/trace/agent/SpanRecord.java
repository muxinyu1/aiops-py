package com.aiops.trace.agent;

/**
 * Data record for a single span in the execution trace.
 */
public class SpanRecord {
    public String span_id;
    public String parent_span_id;
    public String trace_id;
    public String content;       // ClassName.methodName
    public String function;      // methodName
    public String method_signature; // methodName(ParamType1, ParamType2)
    public String class_namespace;  // fully qualified class name
    public String src_file;      // SimpleClassName.java
    public int line_number;
    public long start_ns;
    public long duration_ns;
    public boolean is_error;
    public String error_message;

    // ── 运行时变量快照 (仅在 snapshot 模式下填充) ──────────────────
    public String args_snapshot;        // 方法参数值的 JSON 快照, e.g. {"0":"hello","1":"42"}
    public String return_snapshot;      // 返回值的 JSON 快照
    public String this_snapshot;        // this 对象字段的 JSON 快照

    public String toJson() {
        StringBuilder sb = new StringBuilder();
        sb.append("{");
        sb.append("\"span_id\":\"").append(escape(span_id)).append("\",");
        sb.append("\"parent_span_id\":\"").append(escape(parent_span_id)).append("\",");
        sb.append("\"trace_id\":\"").append(escape(trace_id)).append("\",");
        sb.append("\"content\":\"").append(escape(content)).append("\",");
        sb.append("\"function\":\"").append(escape(function)).append("\",");
        sb.append("\"method_signature\":\"").append(escape(method_signature)).append("\",");
        sb.append("\"class_namespace\":\"").append(escape(class_namespace)).append("\",");
        sb.append("\"src_file\":\"").append(escape(src_file)).append("\",");
        sb.append("\"line_number\":").append(line_number).append(",");
        sb.append("\"start_ns\":").append(start_ns).append(",");
        sb.append("\"duration_ns\":").append(duration_ns).append(",");
        sb.append("\"is_error\":").append(is_error).append(",");
        sb.append("\"error_message\":").append(error_message == null ? "null" : "\"" + escape(error_message) + "\"");
        // 运行时变量快照 (仅在非 null 时输出)
        if (args_snapshot != null) {
            sb.append(",\"args_snapshot\":").append(args_snapshot); // 已经是 JSON 对象
        }
        if (return_snapshot != null) {
            sb.append(",\"return_snapshot\":").append(return_snapshot);
        }
        if (this_snapshot != null) {
            sb.append(",\"this_snapshot\":").append(this_snapshot);
        }
        sb.append("}");
        return sb.toString();
    }

    private static String escape(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    public static String toJsonArray(Iterable<SpanRecord> spans) {
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (SpanRecord span : spans) {
            if (!first) sb.append(",");
            sb.append(span.toJson());
            first = false;
        }
        sb.append("]");
        return sb.toString();
    }
}
