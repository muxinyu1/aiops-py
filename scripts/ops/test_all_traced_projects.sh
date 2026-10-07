#!/bin/bash
# Test dual-tracing (method call stack + line-level coverage) on all 18 projects
set -e

REGISTRY="crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz"
COMPOSE_DIR="/home/mxy/Documents/aiops-py/examples-yml"

# Project definitions: "name:endpoint"
PROJECTS=(
    "Apollo:/apps"
    "Cloud-Platform:/user/front/info"
    "gulimall-learning:/__trace_smoke"
    "java-microservice:/api/health"
    "lamp-cloud:/anyTenant/captcha"
    "mall4cloud:/__trace_smoke"
    "mall-swarm:/__trace_smoke"
    "MoGuBlog:/index/getWebConfig"
    "novel-cloud:/api/front/book/category/list?workDirection=0"
    "PassJava-Platform:/__trace_smoke"
    "pig:/__trace_smoke"
    "PiggyMetrics:/demo"
    "RuoYi-Cloud:/__trace_smoke"
    "RuoYi-Cloud-Plus:/code"
    "SpringBlade:/__trace_smoke"
    "youlai-mall:/api/v1/auth/captcha"
    "yudao-cloud:/admin-api/system/captcha/get"
    "zlt-microservices-platform:/oauth/token"
)

SUCCESS_COUNT=0
FAIL_COUNT=0

echo "=========================================="
echo "Testing dual tracing on 18 projects"
echo "=========================================="

for project_spec in "${PROJECTS[@]}"; do
    IFS=':' read -r PROJECT ENDPOINT <<< "$project_spec"
    
    echo ""
    echo "---------- [$((SUCCESS_COUNT + FAIL_COUNT + 1))/18] $PROJECT ----------"
    
    COMPOSE_FILE="$COMPOSE_DIR/$PROJECT/compose.real.yaml"
    if [[ ! -f "$COMPOSE_FILE" ]]; then
        echo "  ✗ compose file not found: $COMPOSE_FILE"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        continue
    fi
    
    # Start the project
    echo "  Starting $PROJECT..."
    cd "$COMPOSE_DIR/$PROJECT"
    if ! docker compose up -d > /tmp/test_${PROJECT}_start.log 2>&1; then
        echo "  ✗ Failed to start"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        continue
    fi
    
    # Wait for startup
    echo "  Waiting for startup (30s)..."
    sleep 30
    
    # Test the endpoint with X-Return-Trace header
    echo "  Testing $ENDPOINT..."
    HTTP_CODE=$(curl -s -o /tmp/test_${PROJECT}_response.txt -w "%{http_code}" \
        -H "X-Return-Trace: true" \
        "http://127.0.0.1:8080${ENDPOINT}" 2>/dev/null || echo "000")
    
    if [[ "$HTTP_CODE" == "200" ]] || [[ "$HTTP_CODE" == "201" ]]; then
        # Check for trace headers
        HAS_TRACE=$(curl -s -I -H "X-Return-Trace: true" "http://127.0.0.1:8080${ENDPOINT}" 2>/dev/null | grep -i "X-Execution-Trace\|X-Coverage-Data" || echo "")
        
        if [[ -n "$HAS_TRACE" ]]; then
            echo "  ✓ HTTP $HTTP_CODE - Dual tracing OK"
            echo "    Headers: $(echo "$HAS_TRACE" | tr '\n' ' ')"
            SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        else
            echo "  ⚠  HTTP $HTTP_CODE - Missing trace headers"
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    else
        echo "  ✗ HTTP $HTTP_CODE - Endpoint failed"
        tail -3 /tmp/test_${PROJECT}_response.txt 2>/dev/null
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
    
    # Stop the project
    echo "  Stopping $PROJECT..."
    docker compose down > /dev/null 2>&1
    
    # Brief pause between projects
    sleep 3
done

echo ""
echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo "Success: $SUCCESS_COUNT / 18"
echo "Failed:  $FAIL_COUNT / 18"

if [[ $FAIL_COUNT -gt 0 ]]; then
    exit 1
fi

echo ""
echo "All projects validated successfully!"
