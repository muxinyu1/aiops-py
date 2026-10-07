#!/usr/bin/env bash
# 一键构建并推送 MVC 微服务（带执行路径追踪）
# 用法:
#   ./build_mvc_traced_and_push.sh                # 构建全部 MVC 目标
#   ./build_mvc_traced_and_push.sh pig            # 只构建指定项目（支持大小写）
#   RETRY=2 ./build_mvc_traced_and_push.sh all    # 每个项目失败重试 2 次

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
PY_SCRIPT="$ROOT_DIR/batch_trace_integration.py"
LOG_DIR="$ROOT_DIR/logs/build_mvc"
mkdir -p "$LOG_DIR"

SUCCESS_LOG="$ROOT_DIR/examples/build_success.log"
FAILED_LOG="$ROOT_DIR/examples/build_failed.log"
SUMMARY_LOG="$LOG_DIR/summary_$(date +%Y%m%d_%H%M%S).log"

RETRY="${RETRY:-1}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

if [[ ! -f "$PY_SCRIPT" ]]; then
  error "未找到 $PY_SCRIPT"
  exit 1
fi

# 仅 MVC 项目（RuoYi-Cloud-Plus 属于 Dubbo/Hybrid，暂不纳入）
PROJECTS=(
  "pig"
  "RuoYi-Cloud"
  "mall-swarm"
  "SpringBlade"
  "youlai-mall"
  "mall4cloud"
  "zlt-microservices-platform"
  "Apollo"
  "novel-cloud"
  "yudao-cloud"
  "PiggyMetrics"
  "MoGuBlog"
  "Cloud-Platform"
  "PassJava-Platform"
  "gulimall-learning"
  "lamp-cloud"
)

# 兼容大小写输入
normalize_project() {
  local input="$1"
  for p in "${PROJECTS[@]}"; do
    if [[ "${p,,}" == "${input,,}" ]]; then
      echo "$p"
      return 0
    fi
  done
  return 1
}

run_one() {
  local project="$1"
  local attempt=1
  local log_file="$LOG_DIR/${project//\//_}.log"

  while (( attempt <= RETRY )); do
    info "[$project] 开始构建+推送（attempt $attempt/$RETRY）"

    # 直接实时输出到终端，同时落盘
    if (cd "$ROOT_DIR" && python3 "$PY_SCRIPT" "$project") 2>&1 | tee -a "$log_file"; then
      info "[$project] ✅ 完成"
      echo "$project" >> "$SUMMARY_LOG"
      return 0
    fi

    warn "[$project] 本次失败"
    ((attempt++))
    if (( attempt <= RETRY )); then
      warn "[$project] 准备重试..."
    fi
  done

  error "[$project] ❌ 最终失败"
  return 1
}

FILTER="${1:-all}"

# 清理旧统计（与 batch_trace_integration.py 对齐）
: > "$SUCCESS_LOG"
: > "$FAILED_LOG"
: > "$SUMMARY_LOG"

ok=0
fail=0

if [[ "$FILTER" == "all" ]]; then
  TARGET_RUN=("${PROJECTS[@]}")
else
  if proj=$(normalize_project "$FILTER"); then
    TARGET_RUN=("$proj")
  else
    error "未知项目: $FILTER"
    echo "可选项目:"
    printf '  - %s\n' "${PROJECTS[@]}"
    exit 1
  fi
fi

info "将构建 MVC 项目: ${TARGET_RUN[*]}"
info "日志目录: $LOG_DIR"

for p in "${TARGET_RUN[@]}"; do
  if run_one "$p"; then
    ((ok++))
  else
    ((fail++))
  fi

done

echo
info "=== MVC 构建结束 ==="
info "成功项目数: $ok"
info "失败项目数: $fail"
info "镜像成功清单: $SUCCESS_LOG"
info "镜像失败清单: $FAILED_LOG"
info "项目摘要清单: $SUMMARY_LOG"

if (( fail > 0 )); then
  warn "有失败项目，请查看对应日志: $LOG_DIR/*.log"
  exit 2
fi
