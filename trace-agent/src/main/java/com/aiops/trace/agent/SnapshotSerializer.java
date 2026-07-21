package com.aiops.trace.agent;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.*;

/**
 * 轻量级对象序列化工具 — 将 Java 对象序列化为 JSON 字符串用于变量快照.
 *
 * 设计原则:
 *   - 1层深度: 基本类型/String 直接序列化, 复杂对象只取直接字段
 *   - 安全: 截断过长字符串, 限制集合/数组大小, 处理循环引用
 *   - 零外部依赖: 纯 JDK 实现
 *   - 容错: 任何反射/序列化异常都不会传播, 返回 fallback 值
 */
public class SnapshotSerializer {

    /** 单个字符串值的最大长度 */
    private static final int MAX_STRING_LENGTH = 200;

    /** 集合/数组最多序列化的元素数 */
    private static final int MAX_COLLECTION_SIZE = 10;

    /** 对象字段最多序列化的个数 */
    private static final int MAX_FIELDS = 20;

    /** 整个快照 JSON 的最大长度 */
    private static final int MAX_TOTAL_LENGTH = 2000;

    // ═══════════════════════════════════════════════════════════════
    // 公开 API
    // ═══════════════════════════════════════════════════════════════

    /**
     * 序列化方法参数数组为 JSON 对象.
     * 格式: {"0": <value>, "1": <value>, ...}
     * paramNames 为参数名 (如果可用), 否则用下标.
     */
    public static String serializeArgs(Object[] args, String[] paramNames) {
        if (args == null || args.length == 0) {
            return null;
        }
        StringBuilder sb = new StringBuilder("{");
        for (int i = 0; i < args.length; i++) {
            if (i > 0) sb.append(",");
            String key = (paramNames != null && i < paramNames.length && paramNames[i] != null)
                    ? paramNames[i] : String.valueOf(i);
            sb.append("\"").append(escapeJson(key)).append("\":");
            sb.append(serializeValue(args[i]));
            if (sb.length() > MAX_TOTAL_LENGTH) {
                sb.append(",\"_truncated\":true");
                break;
            }
        }
        sb.append("}");
        return sb.toString();
    }

    /**
     * 序列化返回值为 JSON.
     * 格式: {"value": <serialized>, "type": "ClassName"}
     */
    public static String serializeReturn(Object returnValue) {
        if (returnValue == null) {
            return "{\"value\":null}";
        }
        StringBuilder sb = new StringBuilder("{");
        sb.append("\"type\":\"").append(escapeJson(returnValue.getClass().getSimpleName())).append("\",");
        sb.append("\"value\":").append(serializeValue(returnValue));
        sb.append("}");
        return truncate(sb.toString());
    }

    /**
     * 序列化 this 对象的直接字段为 JSON 对象.
     * 格式: {"fieldName": <value>, ...}
     * 只取实例字段 (非 static, 非 transient), 深度 = 1.
     */
    public static String serializeThis(Object thisObj) {
        if (thisObj == null) {
            return null;
        }
        try {
            StringBuilder sb = new StringBuilder("{");
            sb.append("\"_class\":\"").append(escapeJson(thisObj.getClass().getSimpleName())).append("\"");

            Field[] fields = getAllInstanceFields(thisObj.getClass());
            int count = 0;
            for (Field f : fields) {
                if (count >= MAX_FIELDS) {
                    sb.append(",\"_truncated_fields\":true");
                    break;
                }
                int mod = f.getModifiers();
                if (Modifier.isStatic(mod) || Modifier.isTransient(mod)) {
                    continue;
                }
                // 跳过合成字段 (如 this$0)
                if (f.isSynthetic() || f.getName().startsWith("this$")) {
                    continue;
                }
                try {
                    f.setAccessible(true);
                    Object val = f.get(thisObj);
                    sb.append(",\"").append(escapeJson(f.getName())).append("\":");
                    sb.append(serializeValueShallow(val));
                    count++;
                } catch (Exception ignore) {
                    // 无法访问的字段跳过
                }
                if (sb.length() > MAX_TOTAL_LENGTH) {
                    sb.append(",\"_truncated\":true");
                    break;
                }
            }
            sb.append("}");
            return sb.toString();
        } catch (Exception e) {
            return "{\"_error\":\"" + escapeJson(e.getMessage()) + "\"}";
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // 内部序列化逻辑
    // ═══════════════════════════════════════════════════════════════

    /**
     * 序列化单个值 (1层深度).
     * 基本类型/String → 直接值
     * 集合/数组 → 展开元素 (每个元素 shallow)
     * 其他对象 → 取直接字段
     */
    private static String serializeValue(Object obj) {
        if (obj == null) return "null";

        Class<?> clz = obj.getClass();

        // 基本类型包装
        if (obj instanceof Number || obj instanceof Boolean) {
            return obj.toString();
        }
        if (obj instanceof Character) {
            return "\"" + escapeJson(obj.toString()) + "\"";
        }
        if (obj instanceof String) {
            return "\"" + escapeJson(truncateStr((String) obj)) + "\"";
        }
        if (clz.isEnum()) {
            return "\"" + escapeJson(((Enum<?>) obj).name()) + "\"";
        }

        // 数组
        if (clz.isArray()) {
            return serializeArray(obj);
        }

        // Map
        if (obj instanceof Map) {
            return serializeMap((Map<?, ?>) obj);
        }

        // Collection (List, Set, etc.)
        if (obj instanceof Collection) {
            return serializeCollection((Collection<?>) obj);
        }

        // Optional
        if (obj instanceof Optional) {
            Optional<?> opt = (Optional<?>) obj;
            if (opt.isPresent()) {
                return "{\"present\":true,\"value\":" + serializeValueShallow(opt.get()) + "}";
            } else {
                return "{\"present\":false}";
            }
        }

        // Date/Time
        if (obj instanceof Date || obj instanceof Calendar) {
            return "\"" + escapeJson(obj.toString()) + "\"";
        }

        // 其他复杂对象: 取直接字段 (shallow)
        return serializeObjectFields(obj);
    }

    /**
     * 浅层序列化 — 只用 toString, 不递归展开字段.
     * 用于集合元素和嵌套对象的字段值.
     */
    private static String serializeValueShallow(Object obj) {
        if (obj == null) return "null";
        if (obj instanceof Number || obj instanceof Boolean) return obj.toString();
        if (obj instanceof String) return "\"" + escapeJson(truncateStr((String) obj)) + "\"";
        if (obj instanceof Character) return "\"" + escapeJson(obj.toString()) + "\"";
        if (obj.getClass().isEnum()) return "\"" + escapeJson(((Enum<?>) obj).name()) + "\"";

        // 对于复杂对象, 只用 toString
        try {
            String s = obj.toString();
            return "\"" + escapeJson(truncateStr(s)) + "\"";
        } catch (Exception e) {
            return "\"<toString error: " + escapeJson(e.getClass().getSimpleName()) + ">\"";
        }
    }

    private static String serializeArray(Object arr) {
        int len = Array.getLength(arr);
        StringBuilder sb = new StringBuilder("[");
        int limit = Math.min(len, MAX_COLLECTION_SIZE);
        for (int i = 0; i < limit; i++) {
            if (i > 0) sb.append(",");
            sb.append(serializeValueShallow(Array.get(arr, i)));
        }
        if (len > limit) {
            sb.append(",\"...(").append(len).append(" total)\"");
        }
        sb.append("]");
        return sb.toString();
    }

    private static String serializeCollection(Collection<?> coll) {
        StringBuilder sb = new StringBuilder("[");
        int count = 0;
        for (Object item : coll) {
            if (count > 0) sb.append(",");
            if (count >= MAX_COLLECTION_SIZE) {
                sb.append("\"...(").append(coll.size()).append(" total)\"");
                break;
            }
            sb.append(serializeValueShallow(item));
            count++;
        }
        sb.append("]");
        return sb.toString();
    }

    private static String serializeMap(Map<?, ?> map) {
        StringBuilder sb = new StringBuilder("{");
        int count = 0;
        for (Map.Entry<?, ?> entry : map.entrySet()) {
            if (count > 0) sb.append(",");
            if (count >= MAX_COLLECTION_SIZE) {
                sb.append("\"_size\":").append(map.size());
                break;
            }
            String key = entry.getKey() == null ? "null" : truncateStr(entry.getKey().toString());
            sb.append("\"").append(escapeJson(key)).append("\":");
            sb.append(serializeValueShallow(entry.getValue()));
            count++;
        }
        sb.append("}");
        return sb.toString();
    }

    /**
     * 序列化对象的直接字段 (不递归).
     */
    private static String serializeObjectFields(Object obj) {
        try {
            StringBuilder sb = new StringBuilder("{");
            sb.append("\"_type\":\"").append(escapeJson(obj.getClass().getSimpleName())).append("\"");

            Field[] fields = getAllInstanceFields(obj.getClass());
            int count = 0;
            for (Field f : fields) {
                if (count >= MAX_FIELDS) break;
                int mod = f.getModifiers();
                if (Modifier.isStatic(mod) || Modifier.isTransient(mod)) continue;
                if (f.isSynthetic() || f.getName().startsWith("this$")) continue;
                try {
                    f.setAccessible(true);
                    Object val = f.get(obj);
                    sb.append(",\"").append(escapeJson(f.getName())).append("\":");
                    sb.append(serializeValueShallow(val));
                    count++;
                } catch (Exception ignore) {}
            }
            sb.append("}");
            return sb.toString();
        } catch (Exception e) {
            // Fallback to toString
            try {
                return "\"" + escapeJson(truncateStr(obj.toString())) + "\"";
            } catch (Exception e2) {
                return "\"<serialize error>\"";
            }
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // 工具方法
    // ═══════════════════════════════════════════════════════════════

    private static Field[] getAllInstanceFields(Class<?> clz) {
        List<Field> result = new ArrayList<>();
        Class<?> current = clz;
        while (current != null && current != Object.class) {
            try {
                Field[] declared = current.getDeclaredFields();
                for (Field f : declared) {
                    result.add(f);
                }
            } catch (Exception ignore) {}
            current = current.getSuperclass();
        }
        return result.toArray(new Field[0]);
    }

    private static String truncateStr(String s) {
        if (s == null) return "";
        if (s.length() <= MAX_STRING_LENGTH) return s;
        return s.substring(0, MAX_STRING_LENGTH) + "...(" + s.length() + " chars)";
    }

    private static String truncate(String json) {
        if (json.length() <= MAX_TOTAL_LENGTH) return json;
        return json.substring(0, MAX_TOTAL_LENGTH) + "...\"}";
    }

    private static String escapeJson(String s) {
        if (s == null) return "";
        StringBuilder sb = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '"':  sb.append("\\\""); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        return sb.toString();
    }
}
