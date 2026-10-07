#!/bin/bash
# Build and push all 18 traced microservice images to Aliyun ACR
set -e

REGISTRY="crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz"
ROOT_DIR="/home/mxy/Documents/aiops-py/examples"

# Project definitions: "project_dir:dockerfile:image_name"
PROJECTS=(
    "Apollo:Dockerfile.apollo-adminservice:apollo-apollo-adminservice"
    "Cloud-Platform:Dockerfile.ace-admin:ace-admin"
    "gulimall-learning:Dockerfile.gulimall-member:gulimall-member"
    "java-microservice:Dockerfile:java-microservice"
    "lamp-cloud:Dockerfile.lamp-oauth-server:lamp-oauth-server"
    "mall4cloud:Dockerfile.mall4cloud-auth:mall4cloud-auth"
    "mall-swarm:Dockerfile.mall-admin:mall-admin"
    "MoGuBlog:Dockerfile.mogu_admin:mogu_admin"
    "novel-cloud:Dockerfile.novel-book-service:novel-book-service"
    "PassJava-Platform:Dockerfile.passjava-member:passjava-member"
    "pig:Dockerfile.pig-auth:pig-auth"
    "PiggyMetrics:Dockerfile.account-service:account-service"
    "RuoYi-Cloud:Dockerfile.ruoyi-auth:ruoyi-auth"
    "RuoYi-Cloud-Plus:Dockerfile.ruoyi-auth:ruoyi-cloud-plus-auth"
    "SpringBlade:Dockerfile.blade-auth:blade-auth"
    "youlai-mall:Dockerfile.youlai-auth:youlai-auth"
    "yudao-cloud:Dockerfile.yudao-module-system-server:yudao-module-system-server"
    "zlt-microservices-platform:Dockerfile.zlt-uaa:zlt-uaa"
)

SUCCESS_COUNT=0
FAIL_COUNT=0
FAILED_PROJECTS=()

echo "=========================================="
echo "Building and pushing 18 traced images"
echo "Registry: $REGISTRY"
echo "=========================================="

for project_spec in "${PROJECTS[@]}"; do
    IFS=':' read -r PROJECT DOCKERFILE IMAGE <<< "$project_spec"
    
    echo ""
    echo "---------- [$((SUCCESS_COUNT + FAIL_COUNT + 1))/18] $PROJECT ----------"
    
    PROJECT_DIR="$ROOT_DIR/$PROJECT"
    if [[ ! -d "$PROJECT_DIR" ]]; then
        echo "  ERROR: Directory not found: $PROJECT_DIR"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_PROJECTS+=("$PROJECT (dir not found)")
        continue
    fi
    
    DOCKERFILE_PATH="$PROJECT_DIR/$DOCKERFILE"
    if [[ ! -f "$DOCKERFILE_PATH" ]]; then
        echo "  ERROR: Dockerfile not found: $DOCKERFILE_PATH"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_PROJECTS+=("$PROJECT (dockerfile not found)")
        continue
    fi
    
    IMAGE_TAG="$REGISTRY/$IMAGE:latest"
    
    echo "  Building: $IMAGE_TAG"
    if docker build -f "$DOCKERFILE_PATH" -t "$IMAGE_TAG" "$PROJECT_DIR" > "/tmp/build_${IMAGE}.log" 2>&1; then
        echo "  ✓ Build succeeded"
        
        echo "  Pushing to registry..."
        if docker push "$IMAGE_TAG" > "/tmp/push_${IMAGE}.log" 2>&1; then
            echo "  ✓ Push succeeded"
            SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        else
            echo "  ✗ Push failed (see /tmp/push_${IMAGE}.log)"
            FAIL_COUNT=$((FAIL_COUNT + 1))
            FAILED_PROJECTS+=("$PROJECT (push failed)")
        fi
    else
        echo "  ✗ Build failed (see /tmp/build_${IMAGE}.log)"
        tail -20 "/tmp/build_${IMAGE}.log"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_PROJECTS+=("$PROJECT (build failed)")
    fi
done

echo ""
echo "=========================================="
echo "Build & Push Summary"
echo "=========================================="
echo "Success: $SUCCESS_COUNT / 18"
echo "Failed:  $FAIL_COUNT / 18"

if [[ $FAIL_COUNT -gt 0 ]]; then
    echo ""
    echo "Failed projects:"
    for failed in "${FAILED_PROJECTS[@]}"; do
        echo "  - $failed"
    done
    exit 1
fi

echo ""
echo "All images built and pushed successfully!"
