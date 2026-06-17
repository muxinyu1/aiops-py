#!/bin/bash
# 批量克隆微服务示例项目
cd "$(dirname "$0")"

clone_repo() {
    local url=$1
    local dir=$2
    if [ -d "$dir" ]; then
        echo "✓ $dir already exists, skipping"
    else
        echo "→ Cloning $dir ..."
        git clone --depth 1 "$url" "$dir" 2>&1 | tail -1
        if [ $? -eq 0 ]; then
            echo "✓ $dir done"
        else
            echo "✗ $dir FAILED"
        fi
    fi
}

# 1. pig - SB 4.0.6 + SC 2025.1.1 + SCA 2025.1.0.0 | Nacos | Spring Cloud Gateway | Spring Security OAuth2
clone_repo "https://gitee.com/log4j/pig.git" "pig"

# 2. lamp-cloud - SB + SCA + SC | Nacos | Spring Cloud Gateway | Sa-Token
clone_repo "https://gitee.com/zuihou111/lamp-cloud.git" "lamp-cloud"

# 3. zlt-microservices-platform - SB 3.1.6 + SC 2022.0.4 + SCA 2022.0.0.0 | Nacos | Spring Cloud Gateway | Spring Authorization Server
clone_repo "https://gitee.com/zlt2000/microservices-platform.git" "zlt-microservices-platform"

# 4. RuoYi-Cloud-Plus - SB 3.5.14 + SC 2025.0.2 + SCA | Nacos | Spring Cloud Gateway | Sa-Token
clone_repo "https://gitee.com/dromara/RuoYi-Cloud-Plus.git" "RuoYi-Cloud-Plus"

# 5. PassJava-Platform - SB 2.2.6 + SC Hoxton.SR3 + SCA 2.2.0.RELEASE | Nacos + Eureka | Spring Cloud Gateway | JWT / Shiro
clone_repo "https://github.com/Jackson0714/PassJava-Platform.git" "PassJava-Platform"

# 6. spring-cloud-examples (替代spring-boot-cloud) - SC 示例集合 | Eureka | Zuul | Spring Security OAuth2
clone_repo "https://github.com/ityouknow/spring-cloud-examples.git" "spring-cloud-examples"

# 7. youlai-mall - SB 3.2.3 + SC 2023.0.0 + SCA 2023.0.0.0 | Nacos | Spring Cloud Gateway | Spring Authorization Server
clone_repo "https://gitee.com/youlaiorg/youlai-mall.git" "youlai-mall"

# 8. gulimall-learning - SB 2.1.8 + SC Greenwich + SCA 2.1.0.RELEASE | Nacos | Spring Cloud Gateway | Shiro / JWT
clone_repo "https://github.com/NiceSeason/gulimall-learning.git" "gulimall-learning"

# 9. SuperMarket - SB 2.2.5 + SC Hoxton.SR3 | Eureka | Zuul | 自定义Cookie+Redis
# clone_repo "https://gitee.com/zhanglingde/SuperMarket.git" "SuperMarket"  # 仓库不存在，已注释

# 10. Apollo - SB 4.0.5 + SC 2025.1.1 (Eureka) | Eureka | 无网关 | 自定义 + Spring Security
clone_repo "https://github.com/apolloconfig/apollo.git" "Apollo"

# 11. MoGu Blog - SB 2.2.5 + SC Hoxton.SR3 + SCA 2.2.4.RELEASE | Nacos | Spring Cloud Gateway | Spring Security + JWT
clone_repo "https://gitee.com/moxi159753/mogu_blog_v2.git" "MoGuBlog"

# 12. light-reading-cloud - SB 2.1.5 + SC Greenwich.SR1 | Nacos + Eureka | Spring Cloud Gateway | JWT
# clone_repo "https://github.com/haoxiaoyong1014/light-reading-cloud.git" "light-reading-cloud"  # 仓库不存在，已注释

# 13. novel-cloud - SB 3.0.5 + SC 2022.0.1 + SCA 2022.0.0.0-RC1 | Nacos | Spring Cloud Gateway | 自定义JWT
clone_repo "https://github.com/201206030/novel-cloud.git" "novel-cloud"

# 14. RuoYi-Cloud (若依) - SB 4.0.3 + SC 2025.1.0 + SCA 2025.1.0.0 | Nacos | Spring Cloud Gateway | 自定义JWT
clone_repo "https://gitee.com/y_project/RuoYi-Cloud.git" "RuoYi-Cloud"

# 15. yudao-cloud - SB 2.7.18 + SC 2021.0.9 + SCA 2021.0.6.2 | Nacos | Spring Cloud Gateway | 自定义Token + Spring Security
clone_repo "https://gitee.com/zhijiantianya/yudao-cloud.git" "yudao-cloud"

# 16. PiggyMetrics - SB 2.0.3 + SC Finchley.RELEASE | Eureka / Config | Zuul | Spring Security OAuth2
clone_repo "https://github.com/sqshq/piggymetrics.git" "PiggyMetrics"

# 17. mall-swarm - SB 3.5.14 + SC 2025.0.2 + SCA 2025.0.0.0 | Nacos | Spring Cloud Gateway | Sa-Token
clone_repo "https://github.com/macrozheng/mall-swarm.git" "mall-swarm"

# 18. paascloud-master - SB 1.5.13 + SC Edgware.SR3 | Eureka | Zuul | Spring Security OAuth2
clone_repo "https://github.com/paascloud/paascloud-master.git" "paascloud-master"

# 19. SpringBlade - SB 3.2.10 + SCA | Nacos | Spring Cloud Gateway | 自研 JWT + SM2
clone_repo "https://gitee.com/smallc/SpringBlade.git" "SpringBlade"

# 20. Cloud-Platform (ace-security) - SB 2.4.1 + SC 2020.0.0 + SCA 2.2.4.RELEASE | Eureka / Nacos | Spring Cloud Gateway | 自定义JWT
clone_repo "https://gitee.com/geek_qi/cloud-platform.git" "Cloud-Platform"

# 21. mall4cloud - SB 4.0.3 + SC 2025.1.1 + SCA 2025.1.0.0 | Nacos | Spring Cloud Gateway | 自定义Token
clone_repo "https://gitee.com/gz-yami/mall4cloud.git" "mall4cloud"

# 22. SiteWhere - Quarkus 1.7.2 + gRPC + Kafka | gRPC | 无网关 | 自定义 Basic / JWT
clone_repo "https://github.com/sitewhere/sitewhere.git" "SiteWhere"

echo ""
echo "=== Clone Summary ==="
ls -d */ | while read dir; do
    echo "  ✓ $dir"
done
