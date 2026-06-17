#!/bin/bash
# 自动化构建和推送微服务镜像到阿里云 ACR
# 使用方式: ./build_and_push_all.sh [项目名] 或 ./build_and_push_all.sh all

set -e  # 遇到错误立即停止

# 阿里云镜像仓库配置
REGISTRY="crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com"
NAMESPACE="aiops-examples"
TAG="${TAG:-latest}"

# 日志颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 构建单个服务模块
build_service() {
    local project=$1
    local module=$2
    local dockerfile=$3
    local image_name="${REGISTRY}/${NAMESPACE}/${project}-${module}:${TAG}"
    
    log_info "Building $project/$module -> $image_name"
    
    # 构建镜像
    cd "$project"
    if docker build -f "$dockerfile" -t "$image_name" .; then
        log_info "✅ Built $image_name"
        
        # 推送到阿里云
        if docker push "$image_name"; then
            log_info "✅ Pushed $image_name"
            echo "$image_name" >> ../build_success.log
            return 0
        else
            log_error "❌ Failed to push $image_name"
            return 1
        fi
    else
        log_error "❌ Failed to build $image_name"
        return 1
    fi
    cd ..
}

# 构建 Maven 项目
build_maven_project() {
    local project=$1
    log_info "Maven building $project ..."
    
    cd "$project"
    if mvn clean package -DskipTests -T 4 2>&1 | tee "../${project}_maven.log" | grep -E "BUILD SUCCESS|BUILD FAILURE"; then
        log_info "✅ Maven build completed for $project"
        cd ..
        return 0
    else
        log_error "❌ Maven build failed for $project"
        cd ..
        return 1
    fi
}

# pig 项目
build_pig() {
    log_info "=== Building pig ==="
    build_maven_project "pig" || return 1
    
    # pig 主要服务
    build_service "pig" "gateway" "pig-gateway/Dockerfile"
    build_service "pig" "auth" "pig-auth/Dockerfile"
    build_service "pig" "upms" "pig-upms/pig-upms-biz/Dockerfile"
    build_service "pig" "register" "pig-register/Dockerfile"
}

# RuoYi-Cloud 项目
build_ruoyi_cloud() {
    log_info "=== Building RuoYi-Cloud ==="
    build_maven_project "RuoYi-Cloud" || return 1
    
    # RuoYi-Cloud 主要服务
    build_service "RuoYi-Cloud" "gateway" "ruoyi-gateway/Dockerfile"
    build_service "RuoYi-Cloud" "auth" "ruoyi-auth/Dockerfile"
    build_service "RuoYi-Cloud" "system" "ruoyi-modules/ruoyi-system/Dockerfile"
}

# RuoYi-Cloud-Plus 项目
build_ruoyi_cloud_plus() {
    log_info "=== Building RuoYi-Cloud-Plus ==="
    build_maven_project "RuoYi-Cloud-Plus" || return 1
    
    # RuoYi-Cloud-Plus 主要服务
    build_service "RuoYi-Cloud-Plus" "gateway" "ruoyi-gateway/Dockerfile"
    build_service "RuoYi-Cloud-Plus" "auth" "ruoyi-auth/Dockerfile"
    build_service "RuoYi-Cloud-Plus" "system" "ruoyi-modules/ruoyi-system/Dockerfile"
    build_service "RuoYi-Cloud-Plus" "gen" "ruoyi-modules/ruoyi-gen/Dockerfile"
    build_service "RuoYi-Cloud-Plus" "job" "ruoyi-modules/ruoyi-job/Dockerfile"
}

# mall-swarm 项目
build_mall_swarm() {
    log_info "=== Building mall-swarm ==="
    build_maven_project "mall-swarm" || return 1
    
    # mall-swarm 主要服务
    build_service "mall-swarm" "gateway" "mall-gateway/Dockerfile"
    build_service "mall-swarm" "auth" "mall-auth/Dockerfile"
    build_service "mall-swarm" "admin" "mall-admin/Dockerfile"
    build_service "mall-swarm" "portal" "mall-portal/Dockerfile"
    build_service "mall-swarm" "search" "mall-search/Dockerfile"
}

# SpringBlade 项目
build_springblade() {
    log_info "=== Building SpringBlade ==="
    build_maven_project "SpringBlade" || return 1
    
    # SpringBlade 主要服务
    build_service "SpringBlade" "auth" "blade-auth/Dockerfile"
    build_service "SpringBlade" "gateway" "blade-gateway/Dockerfile"
    build_service "SpringBlade" "system" "blade-service/blade-system/Dockerfile"
    build_service "SpringBlade" "desk" "blade-service/blade-desk/Dockerfile"
}

# PiggyMetrics 项目
build_piggymetrics() {
    log_info "=== Building PiggyMetrics ==="
    build_maven_project "PiggyMetrics" || return 1
    
    # PiggyMetrics 主要服务
    build_service "PiggyMetrics" "gateway" "gateway/Dockerfile"
    build_service "PiggyMetrics" "auth-service" "auth-service/Dockerfile"
    build_service "PiggyMetrics" "account-service" "account-service/Dockerfile"
    build_service "PiggyMetrics" "statistics-service" "statistics-service/Dockerfile"
    build_service "PiggyMetrics" "notification-service" "notification-service/Dockerfile"
    build_service "PiggyMetrics" "config" "config/Dockerfile"
    build_service "PiggyMetrics" "registry" "registry/Dockerfile"
}

# yudao-cloud 项目
build_yudao_cloud() {
    log_info "=== Building yudao-cloud ==="
    build_maven_project "yudao-cloud" || return 1
    
    # yudao-cloud 主要服务
    build_service "yudao-cloud" "gateway" "yudao-gateway/Dockerfile"
    build_service "yudao-cloud" "system" "yudao-module-system/yudao-module-system-biz/Dockerfile"
}

# lamp-cloud 项目
build_lamp_cloud() {
    log_info "=== Building lamp-cloud ==="
    build_maven_project "lamp-cloud" || return 1
    
    # lamp-cloud 主要服务
    build_service "lamp-cloud" "gateway" "lamp-gateway/lamp-gateway-server/Dockerfile"
    build_service "lamp-cloud" "oauth" "lamp-oauth/lamp-oauth-server/Dockerfile"
    build_service "lamp-cloud" "system" "lamp-system/lamp-system-server/Dockerfile"
}

# youlai-mall 项目
build_youlai_mall() {
    log_info "=== Building youlai-mall ==="
    build_maven_project "youlai-mall" || return 1
    
    # youlai-mall 主要服务
    build_service "youlai-mall" "gateway" "youlai-gateway/Dockerfile"
    build_service "youlai-mall" "auth" "youlai-auth/Dockerfile"
    build_service "youlai-mall" "admin" "youlai-admin/admin-boot/Dockerfile"
}

# mall4cloud 项目
build_mall4cloud() {
    log_info "=== Building mall4cloud ==="
    build_maven_project "mall4cloud" || return 1
    
    # mall4cloud 主要服务
    build_service "mall4cloud" "gateway" "mall4cloud-gateway/Dockerfile"
    build_service "mall4cloud" "auth" "mall4cloud-auth/Dockerfile"
    build_service "mall4cloud" "multishop" "mall4cloud-multishop/Dockerfile"
}

# zlt-microservices-platform 项目
build_zlt_platform() {
    log_info "=== Building zlt-microservices-platform ==="
    build_maven_project "zlt-microservices-platform" || return 1
    
    # zlt 主要服务
    build_service "zlt-microservices-platform" "gateway" "zlt-gateway/sc-gateway/Dockerfile"
    build_service "zlt-microservices-platform" "uaa" "zlt-uaa/Dockerfile"
    build_service "zlt-microservices-platform" "user" "zlt-business/user-center/Dockerfile"
}

# Apollo 项目
build_apollo() {
    log_info "=== Building Apollo ==="
    build_maven_project "Apollo" || return 1
    
    # Apollo 主要服务
    build_service "Apollo" "configservice" "apollo-configservice/src/main/docker/Dockerfile"
    build_service "Apollo" "adminservice" "apollo-adminservice/src/main/docker/Dockerfile"
    build_service "Apollo" "portal" "apollo-portal/src/main/docker/Dockerfile"
}

# novel-cloud 项目
build_novel_cloud() {
    log_info "=== Building novel-cloud ==="
    build_maven_project "novel-cloud" || return 1
    
    # novel-cloud 主要服务
    build_service "novel-cloud" "gateway" "novel-gateway/Dockerfile"
    build_service "novel-cloud" "home" "doc/docker/novel-home-service/Dockerfile"
    build_service "novel-cloud" "book" "doc/docker/novel-book-service/Dockerfile"
}

# MoGuBlog 项目
build_mogublog() {
    log_info "=== Building MoGuBlog ==="
    build_maven_project "MoGuBlog" || return 1
    
    # MoGuBlog 主要服务
    build_service "MoGuBlog" "admin" "mogu_admin/src/main/resources/Dockerfile"
    build_service "MoGuBlog" "web" "mogu_web/src/main/resources/Dockerfile"
    build_service "MoGuBlog" "gateway" "mogu_gateway/src/main/resources/Dockerfile"
}

# Cloud-Platform 项目
build_cloud_platform() {
    log_info "=== Building Cloud-Platform ==="
    build_maven_project "Cloud-Platform" || return 1
    
    # Cloud-Platform 主要服务
    build_service "Cloud-Platform" "gate" "ace-gate/src/main/docker/Dockerfile"
    build_service "Cloud-Platform" "admin" "ace-modules/ace-admin/src/main/docker/Dockerfile"
}

# PassJava-Platform 项目
build_passjava() {
    log_info "=== Building PassJava-Platform ==="
    build_maven_project "PassJava-Platform" || return 1
    
    # PassJava 主要服务
    build_service "PassJava-Platform" "gateway" "passjava-gateway/Dockerfile"
    build_service "PassJava-Platform" "member" "passjava-member/Dockerfile"
}

# gulimall-learning 项目
build_gulimall() {
    log_info "=== Building gulimall-learning ==="
    build_maven_project "gulimall-learning" || return 1
    
    # gulimall 主要服务
    build_service "gulimall-learning" "gateway" "gulimall-gateway/Dockerfile"
    build_service "gulimall-learning" "product" "gulimall-product/Dockerfile"
    build_service "gulimall-learning" "member" "gulimall-member/Dockerfile"
}

# 主流程
main() {
    cd "$(dirname "$0")"
    
    # 清空日志
    > build_success.log
    > build_failed.log
    
    log_info "Docker Registry: $REGISTRY/$NAMESPACE"
    log_info "Image Tag: $TAG"
    echo ""
    
    # 检查是否指定了项目
    if [ $# -eq 0 ] || [ "$1" = "all" ]; then
        log_info "Building all projects..."
        
        # 按优先级顺序构建
        build_pig || log_warn "pig build failed"
        build_ruoyi_cloud || log_warn "RuoYi-Cloud build failed"
        build_ruoyi_cloud_plus || log_warn "RuoYi-Cloud-Plus build failed"
        build_mall_swarm || log_warn "mall-swarm build failed"
        build_springblade || log_warn "SpringBlade build failed"
        build_piggymetrics || log_warn "PiggyMetrics build failed"
        build_yudao_cloud || log_warn "yudao-cloud build failed"
        build_lamp_cloud || log_warn "lamp-cloud build failed"
        build_youlai_mall || log_warn "youlai-mall build failed"
        build_mall4cloud || log_warn "mall4cloud build failed"
        build_zlt_platform || log_warn "zlt-microservices-platform build failed"
        build_apollo || log_warn "Apollo build failed"
        build_novel_cloud || log_warn "novel-cloud build failed"
        build_mogublog || log_warn "MoGuBlog build failed"
        build_cloud_platform || log_warn "Cloud-Platform build failed"
        build_passjava || log_warn "PassJava-Platform build failed"
        build_gulimall || log_warn "gulimall-learning build failed"
        
    else
        # 构建指定项目
        case "$1" in
            pig) build_pig ;;
            ruoyi-cloud) build_ruoyi_cloud ;;
            ruoyi-cloud-plus) build_ruoyi_cloud_plus ;;
            mall-swarm) build_mall_swarm ;;
            springblade) build_springblade ;;
            piggymetrics) build_piggymetrics ;;
            yudao-cloud) build_yudao_cloud ;;
            lamp-cloud) build_lamp_cloud ;;
            youlai-mall) build_youlai_mall ;;
            mall4cloud) build_mall4cloud ;;
            zlt) build_zlt_platform ;;
            apollo) build_apollo ;;
            novel-cloud) build_novel_cloud ;;
            mogublog) build_mogublog ;;
            cloud-platform) build_cloud_platform ;;
            passjava) build_passjava ;;
            gulimall) build_gulimall ;;
            *)
                log_error "Unknown project: $1"
                echo "Available projects: pig, ruoyi-cloud, ruoyi-cloud-plus, mall-swarm, springblade, piggymetrics, yudao-cloud, lamp-cloud, youlai-mall, mall4cloud, zlt, apollo, novel-cloud, mogublog, cloud-platform, passjava, gulimall"
                exit 1
                ;;
        esac
    fi
    
    echo ""
    log_info "=== Build Summary ==="
    echo "Successfully built images:"
    cat build_success.log 2>/dev/null || echo "  (none)"
    echo ""
    log_info "Done!"
}

main "$@"
