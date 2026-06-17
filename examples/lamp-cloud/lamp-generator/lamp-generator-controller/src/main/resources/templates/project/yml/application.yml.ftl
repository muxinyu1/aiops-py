${projectPrefix}:
  swagger:
    version: '${r"@"}project.version${r"@"}'
  nacos:
    ip: ${r"${"}NACOS_IP:@nacos.ip@${r"}"}
    port: ${r"${"}NACOS_PORT:@nacos.port@${r"}"}
    namespace: ${r"${"}NACOS_NAMESPACE:@nacos.namespace@${r"}"}
    username: ${r"${"}NACOS_USERNAME:@nacos.username@${r"}"}
    password: ${r"${"}NACOS_PASSWORD:@nacos.password@${r"}"}
    group: DEFAULT_GROUP
  sentinel:
    dashboard: ${r"${SENTINEL_DASHBOARD:@sentinel.dashboard@}"}
  seata:
    ip: ${r"${"}SEATA_IP:@seata.ip@${r"}"}
    port: ${r"${"}SEATA_PORT:@seata.port@${r"}"}
    namespace: ${r"${"}SEATA_NAMESPACE:@seata.namespace@${r"}"}

spring:
  main:
    allow-bean-definition-overriding: true
  application:
    name: ${applicationName}
    # 此参数一定要和 lamp-gateway-server.yml 文件中配置的 路由前缀(predicates参数) 一致！ （可以参考 system或base 服务）
    path: /${serviceName}
  profiles:
    active: ${r"@"}profile.active${r"@"}
  config:
    import:
      - nacos:common.yml?group=${r"${"}${projectPrefix}.nacos.group${"}"}&refresh=true
      - nacos:redis.yml?group=${r"${"}${projectPrefix}.nacos.group${"}"}&refresh=true
      - nacos:${r"@"}database.type${r"@"}?group=${r"${"}${projectPrefix}.nacos.group${"}"}&refresh=true
      - nacos:rabbitmq.yml?group=${r"${"}${projectPrefix}.nacos.group${"}"}&refresh=true
      - nacos:${r"${spring.application.name}"}.yml?refresh=true
  cloud:
    sentinel:
      enabled: true
      filter:
        enabled: true
      eager: true  # 取消Sentinel控制台懒加载
      transport:
        dashboard: ${r"${"}${projectPrefix}.sentinel.dashboard${r"}"}
    nacos:
      server-addr: ${r"${"}${projectPrefix}.nacos.ip${r"}"}:${r"${"}${projectPrefix}.nacos.port${r"}"}
      username: ${r"${"}${projectPrefix}.nacos.username${r"}"}
      password: ${r"${"}${projectPrefix}.nacos.password${r"}"}
      config:
        file-extension: yml
        namespace: ${r"${"}${projectPrefix}.nacos.namespace${r"}"}
        enabled: true
      discovery:
        namespace: ${r"${"}${projectPrefix}.nacos.namespace${r"}"}
        metadata: # 元数据，用于权限服务实时获取各个服务的所有接口
          management.context-path: ${r"${"}server.servlet.context-path:${r"}"}${r"${"}spring.mvc.servlet.path:${r"}"}${r"${"}management.endpoints.web.base-path:${r"}"}
          gray_version: ${pg.author}

# 用于/actuator/info
info:
  name: '${r"@"}project.name${r"@"}'
  description: '${r"@"}project.description${r"@"}'
  version: '${r"@"}project.version${r"@"}'

## 以下配置可以移动到nacos中
server:
  port: ${pg.serverPort?c}

springdoc:
  group-configs:
    - group: '${serviceName}'
      displayName: '${serviceName}服务'
      paths-to-match: '/**'
      packages-to-scan: ${pg.parent}.${moduleName}.controller

