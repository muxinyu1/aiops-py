# 测试流程

## 完整 Python 测试脚本

替换 `BASE_URL` 和 `test_cases` 为目标服务的实际接口，然后运行：

```python
#!/usr/bin/env python3
"""
验证 X-Return-Trace 追踪集成是否正常工作。
用法：python3 test_trace.py http://localhost:8081
"""
import base64, json, sys, urllib.request, urllib.error

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8081"

# ── 修改为目标服务的实际接口 ────────────────────────────────────────────────────
test_cases = [
    # (label,               method, path,             body_bytes)
    ("happy path",          "GET",  "/api/users/1",   None),
    ("not found",           "GET",  "/api/users/999", None),
    ("create (valid)",      "POST", "/api/users",
     b'{"name":"Test","email":"test@example.com"}'),
    ("create (bad email)",  "POST", "/api/users",
     b'{"name":"Bad","email":"not-valid"}'),
]
# ───────────────────────────────────────────────────────────────────────────────

def run_test(label, method, path, body):
    headers = {"X-Return-Trace": "true"}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + path, method=method, headers=headers, data=body)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        resp = e

    status      = resp.status if hasattr(resp, "status") else resp.code
    trace_hdr   = resp.headers.get("X-Execution-Trace")
    resp.read()  # consume body

    print(f"\n>>> {label}  [{method} {path}]")
    print(f"    HTTP {status}  |  X-Execution-Trace: {'✅ present' if trace_hdr else '❌ MISSING'}")

    passed = True
    if trace_hdr:
        try:
            spans = json.loads(base64.b64decode(trace_hdr))
            if not spans:
                print("    ⚠ 0 spans returned")
                passed = False
            for s in spans:
                err = "  ← ERROR" if s.get("is_error") else ""
                print(f"    [{s.get('span_id','?')[:8]}]  "
                      f"{s.get('content','?'):<42} "
                      f"{s.get('src_file','?')}:{s.get('line_number','?')}  "
                      f"{s.get('duration_ns', 0) // 1_000_000}ms{err}")
                # 关键字段非空验证
                for field in ("content", "src_file", "line_number"):
                    if not s.get(field):
                        print(f"    ❌ field '{field}' is empty")
                        passed = False
        except Exception as ex:
            print(f"    ❌ failed to decode trace: {ex}")
            passed = False
    else:
        passed = False

    return passed

all_pass = all(run_test(*tc) for tc in test_cases)
print("\n" + ("=" * 50))
print("Result:", "✅ ALL PASSED" if all_pass else "❌ SOME FAILED")
sys.exit(0 if all_pass else 1)
```

---

## 快速 Shell 验证（单接口）

```bash
SERVICE=http://localhost:8081
API_PATH=/api/users/1      # ← 改为实际接口

ENCODED=$(curl -s -D - \
    -H "X-Return-Trace: true" \
    "${SERVICE}${API_PATH}" \
  | grep -i "x-execution-trace" \
  | awk '{print $2}' | tr -d '\r')

if [ -z "$ENCODED" ]; then
    echo "❌ X-Execution-Trace header not found"
    exit 1
fi

echo "✅ Got X-Execution-Trace, decoding..."
python3 -c "
import base64, json
spans = json.loads(base64.b64decode('${ENCODED}'))
print(f'  {len(spans)} span(s):')
for s in spans:
    err = '  ← ERROR' if s.get('is_error') else ''
    print(f\"  {s['content']:<40} {s['src_file']}:{s['line_number']}  {s['duration_ns']//1_000_000}ms{err}\")
"
```

---

## 验收标准

| 检查项 | 期望值 |
|--------|--------|
| `X-Execution-Trace` 响应头 | 存在 |
| spans 数量 | ≥ 1 |
| `src_file` | 非空，如 `UserService.java` |
| `line_number` | > 0 |
| 错误路径的 `is_error` | `true` |
| 正常路径的 `is_error` | `false` |
| 不携带 `X-Return-Trace` 的请求 | 无 `X-Execution-Trace` 响应头 |

---

## 完整测试流程（含容器启停）

```bash
REGISTRY="crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com"
IMAGE="${REGISTRY}/llmfuzz/<project-name>:latest"

# 1. 启动
docker run -d --name trace-test -p 8081:8080 "$IMAGE"

# 2. 等待健康检查（最多 60s）
for i in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8081/actuator/health)" = "200" ] \
    && echo "Ready after ${i}s" && break
  sleep 1
done

# 3. 运行 Python 测试脚本
python3 test_trace.py http://localhost:8081

# 4. 查看容器日志（如有失败）
docker logs trace-test --tail 50

# 5. 清理
docker rm -f trace-test
```
