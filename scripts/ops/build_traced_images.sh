#!/bin/bash
# 构建所有带追踪能力的微服务镜像并推送到阿里云 ACR
# 用法: ./build_traced_images.sh [project_name|all]

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXAMPLES="$SCRIPT_DIR/examples"
REGISTRY="crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com"
NAMESPACE="llmfuzz"
MAX_PARALLEL=3
LOG_DIR="$SCRIPT_DIR/logs/build_traced"
mkdir -p "$LOG_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# (project, module_last_part, image_name_suffix)
TARGETS=(
  "pig:pig-auth:pig-pig-auth"
  "RuoYi-Cloud:ruoyi-auth:ruoyi-cloud-ruoyi-auth"
  "RuoYi-Cloud-Plus:ruoyi-auth:ruoyi-cloud-plus-ruoyi-auth"
  "mall-swarm:mall-admin:mall-swarm-mall-admin"
  "SpringBlade:blade-auth:springblade-blade-auth"
  "youlai-mall:youlai-auth:youlai-mall-youlai-auth"
  "mall4cloud:mall4cloud-auth:mall4cloud-mall4cloud-auth"
  "zlt-microservices-platform:zlt-uaa:zlt-microservices-platform-zlt-uaa"
  "Apollo:apollo-adminservice:apollo-apollo-adminservice"
  "novel-cloud:novel-book-service:novel-cloud-novel-book-service"
  "yudao-cloud:yudao-module-system-server:yudao-cloud-yudao-module-system-server"
  "PiggyMetrics:account-service:piggymetrics-account-service"
  "MoGuBlog:mogu_admin:mogublog-mogu_admin"
  "Cloud-Platform:ace-admin:cloud-platform-ace-admin"
  "PassJava-Platform:passjava-member:passjava-platform-passjava-member"
  "gulimall-learning:gulimall-member:gulimall-learning-gulimall-member"
  "lamp-cloud:lamp-oauth-server:lamp-cloud-lamp-oauth-server"
)

build_one() {
  local project="$1" module="$2" img_suffix="$3"
  local dockerfile="$EXAMPLES/$project/Dockerfile.$module"
  local image="$REGISTRY/$NAMESPACE/$img_suffix:latest"
  local logfile="$LOG_DIR/${img_suffix}.log"

  if [ ! -f "$dockerfile" ]; then
    warn "[$project] Dockerfile.$module not found, skipping"
    return 1
  fi

  info "[$project/$module] Building → $image"
  if docker build -f "$dockerfile" -t "$image" "$EXAMPLES/$project" \
       > "$logfile" 2>&1; then
    info "[$project/$module] ✅ Build OK"
    if docker push "$image" >> "$logfile" 2>&1; then
      info "[$project/$module] ✅ Pushed"
      echo "$image" >> "$SCRIPT_DIR/examples/build_success.log"
      return 0
    else
      error "[$project/$module] ❌ Push failed (see $logfile)"
      echo "$image" >> "$SCRIPT_DIR/examples/build_failed.log"
      return 1
    fi
  else
    local snippet
    snippet=$(tail -20 "$logfile" 2>/dev/null)
    error "[$project/$module] ❌ Build failed"
    echo "$snippet"
    echo "$image" >> "$SCRIPT_DIR/examples/build_failed.log"
    return 1
  fi
}

filter="${1:-all}"
pids=()
running=0
ok=0; fail=0; skip=0

> "$SCRIPT_DIR/examples/build_success.log"
> "$SCRIPT_DIR/examples/build_failed.log"

for entry in "${TARGETS[@]}"; do
  IFS=':' read -r project module img_suffix <<< "$entry"

  # Filter by project name if specified
  if [[ "$filter" != "all" && "$filter" != "$project" ]]; then
    continue
  fi

  # Wait if at max parallel
  while [ "${#pids[@]}" -ge "$MAX_PARALLEL" ]; do
    new_pids=()
    for p in "${pids[@]}"; do
      if kill -0 "$p" 2>/dev/null; then
        new_pids+=("$p")
      fi
    done
    pids=("${new_pids[@]}")
    [ "${#pids[@]}" -ge "$MAX_PARALLEL" ] && sleep 5
  done

  build_one "$project" "$module" "$img_suffix" &
  pids+=($!)
done

# Wait for all remaining
for p in "${pids[@]}"; do
  wait "$p" || true
done

info "=== DONE ==="
ok=$(wc -l < "$SCRIPT_DIR/examples/build_success.log" 2>/dev/null || echo 0)
fail=$(wc -l < "$SCRIPT_DIR/examples/build_failed.log" 2>/dev/null || echo 0)
info "Success: $ok  Failed: $fail"
[ "$fail" -gt 0 ] && warn "Failed images:" && cat "$SCRIPT_DIR/examples/build_failed.log"
