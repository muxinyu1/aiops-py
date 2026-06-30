#!/usr/bin/env python3
"""Validate X-Return-Trace / X-Execution-Trace for traced example images.

The script starts each image, discovers likely controller endpoints from source,
probes them with X-Return-Trace: true, decodes the response header, and verifies
that at least one span has source file and a positive line number.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples"
EXAMPLES_YML = ROOT / "examples-yml"
REAL_DEPS_COMPOSE = EXAMPLES_YML / "_shared" / "real-deps.compose.yaml"
REGISTRY = "crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com"
NAMESPACE = "llmfuzz"


@dataclass(frozen=True)
class Target:
    project: str
    module: str
    root_package: str
    image: str

    @property
    def name(self) -> str:
        return f"{self.project}/{self.module}" if self.module else self.project

    @property
    def module_dir(self) -> Path:
        return EXAMPLES / self.project / self.module if self.module else EXAMPLES / self.project


TARGETS = [
    Target("pig", "pig-auth", "com.pig4cloud.pig.auth", f"{REGISTRY}/{NAMESPACE}/pig-pig-auth:latest"),
    Target("RuoYi-Cloud", "ruoyi-auth", "com.ruoyi.auth", f"{REGISTRY}/{NAMESPACE}/ruoyi-cloud-ruoyi-auth:latest"),
    Target("RuoYi-Cloud-Plus", "ruoyi-auth", "org.dromara.auth", f"{REGISTRY}/{NAMESPACE}/ruoyi-cloud-plus-ruoyi-auth:latest"),
    Target("mall-swarm", "mall-admin", "com.macro.mall", f"{REGISTRY}/{NAMESPACE}/mall-swarm-mall-admin:latest"),
    Target("SpringBlade", "blade-auth", "org.springblade.auth", f"{REGISTRY}/{NAMESPACE}/springblade-blade-auth:latest"),
    Target("youlai-mall", "youlai-auth", "com.youlai.auth", f"{REGISTRY}/{NAMESPACE}/youlai-mall-youlai-auth:latest"),
    Target("mall4cloud", "mall4cloud-auth", "com.mall4j.cloud.auth", f"{REGISTRY}/{NAMESPACE}/mall4cloud-mall4cloud-auth:latest"),
    Target("zlt-microservices-platform", "zlt-uaa", "com.central", f"{REGISTRY}/{NAMESPACE}/zlt-microservices-platform-zlt-uaa:latest"),
    Target("Apollo", "apollo-adminservice", "com.ctrip.framework.apollo.adminservice", f"{REGISTRY}/{NAMESPACE}/apollo-apollo-adminservice:latest"),
    Target("novel-cloud", "novel-book/novel-book-service", "io.github.xxyopen.novel.book", f"{REGISTRY}/{NAMESPACE}/novel-cloud-novel-book-service:latest"),
    Target("yudao-cloud", "yudao-module-system/yudao-module-system-server", "cn.iocoder.yudao.module.system", f"{REGISTRY}/{NAMESPACE}/yudao-cloud-yudao-module-system-server:latest"),
    Target("PiggyMetrics", "account-service", "com.piggymetrics.account", f"{REGISTRY}/{NAMESPACE}/piggymetrics-account-service:latest"),
    Target("MoGuBlog", "mogu_admin", "com.moxi.mogublog.admin", f"{REGISTRY}/{NAMESPACE}/mogublog-mogu_admin:latest"),
    Target("Cloud-Platform", "ace-modules/ace-admin", "com.github.wxiaoqi.security", f"{REGISTRY}/{NAMESPACE}/cloud-platform-ace-admin:latest"),
    Target("PassJava-Platform", "passjava-member", "com.jackson0714.passjava.member", f"{REGISTRY}/{NAMESPACE}/passjava-platform-passjava-member:latest"),
    Target("gulimall-learning", "gulimall-member", "io.niceseason.gulimall.member", f"{REGISTRY}/{NAMESPACE}/gulimall-learning-gulimall-member:latest"),
    Target("lamp-cloud", "lamp-oauth/lamp-oauth-server", "top.tangyh.lamp", f"{REGISTRY}/{NAMESPACE}/lamp-cloud-lamp-oauth-server:latest"),
    Target("java-microservice", "", "com.example.microservice", f"{REGISTRY}/{NAMESPACE}/java-microservice:latest"),
]


@dataclass
class ProbeResult:
    method: str
    path: str
    status: int | None = None
    trace_present: bool = False
    span_count: int = 0
    valid: bool = False
    error: str = ""
    first_span: str = ""


@dataclass
class TargetResult:
    target: str
    image: str
    container: str
    host_port: int | None = None
    state: str = "unknown"
    success: bool = False
    probe: ProbeResult | None = None
    negative_ok: bool = False
    candidates: int = 0
    logs_tail: str = ""
    notes: list[str] = field(default_factory=list)


def run(cmd: list[str], *, timeout: int = 30, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout, check=check)


def docker_available() -> bool:
    return shutil.which("docker") is not None and run(["docker", "info"], timeout=10).returncode == 0


def sanitize_container_name(name: str) -> str:
    return "trace-validate-" + re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-").lower()[:80]


def split_annotation_value(raw: str) -> list[str]:
    if not raw:
        return [""]
    text = raw.strip()
    if not text or text.startswith("params") or text.startswith("method"):
        return [""]
    values = re.findall(r'"([^"]*)"', text)
    if values:
        return values
    single = re.findall(r"'([^']*)'", text)
    return single or [""]


def normalize_path(path: str) -> str | None:
    if not path or "${" in path or "#" in path:
        return None
    path = path.strip()
    path = re.sub(r"\{[^}/]+\}", "1", path)
    path = path.replace("//", "/")
    path = re.sub(r"/\*\*?$", "", path)
    if not path.startswith("/"):
        path = "/" + path
    path = re.sub(r"/+", "/", path)
    return path or "/"


def join_paths(a: str, b: str) -> str | None:
    if not a:
        a = ""
    if not b:
        b = ""
    return normalize_path("/".join([a.strip("/"), b.strip("/")]).strip("/"))


def discover_candidates(module_dir: Path, max_candidates: int, *, include_smoke: bool = False) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    java_files = sorted(module_dir.glob("src/main/java/**/*.java"))
    mapping_re = re.compile(r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*(?:\(([^)]*)\))?", re.S)
    class_re = re.compile(r"\b(class|interface)\s+\w+")

    for file in java_files:
        text = file.read_text(errors="ignore")
        if ("@Controller" not in text and "@RestController" not in text) or (not include_smoke and "tracesmoke" in file.parts):
            continue

        class_pos = class_re.search(text)
        class_prefixes = [""]
        if class_pos:
            before_class = text[:class_pos.start()]
            class_mappings = list(mapping_re.finditer(before_class))
            if class_mappings:
                class_prefixes = split_annotation_value(class_mappings[-1].group(2) or "")

        for m in mapping_re.finditer(text[class_pos.end() if class_pos else 0:]):
            ann = m.group(1)
            args = m.group(2) or ""
            if ann == "RequestMapping":
                if "RequestMethod.POST" in args:
                    method = "POST"
                elif "RequestMethod.PUT" in args:
                    method = "PUT"
                elif "RequestMethod.DELETE" in args:
                    method = "DELETE"
                elif "RequestMethod.PATCH" in args:
                    method = "PATCH"
                else:
                    method = "GET"
            else:
                method = ann.replace("Mapping", "").upper()

            if method not in {"GET", "POST"}:
                continue
            method_paths = split_annotation_value(args)
            for cp in class_prefixes:
                for mp in method_paths:
                    path = join_paths(cp, mp)
                    if path and path not in {"/error"}:
                        candidates.append((method, path))

    # Prefer low-risk, no-body GET endpoints; include common trace-friendly fallbacks.
    fallback = [("GET", "/"), ("GET", "/actuator/health"), ("GET", "/health")]
    if include_smoke:
        fallback = [("GET", "/__trace_smoke"), ("GET", "/api/trace-smoke")] + fallback
    ordered: list[tuple[str, str]] = []
    smoke_prefix = fallback[:2] if include_smoke else []
    normal_fallback = fallback[2:] if include_smoke else fallback
    for item in smoke_prefix + [x for x in candidates if x[0] == "GET"] + [x for x in candidates if x[0] != "GET"] + normal_fallback:
        if item not in ordered:
            ordered.append(item)
    return ordered[:max_candidates]


def target_preferred_candidates(target: Target) -> list[tuple[str, str]]:
    if target.project == "RuoYi-Cloud-Plus":
        return [
            ("GET", "/code"),
        ]
    if target.project == "youlai-mall":
        return [
            ("GET", "/api/v1/auth/captcha"),
        ]
    if target.project == "novel-cloud":
        return [
            ("GET", "/api/front/book/category/list?workDirection=0"),
            ("GET", "/api/front/book/visit_rank"),
            ("GET", "/api/front/book/newest_rank"),
        ]
    if target.project == "yudao-cloud":
        return [
            ("POST", "/admin-api/system/auth/logout"),
            ("POST", "/admin-api/system/captcha/get"),
        ]
    if target.project == "PiggyMetrics":
        return [
            ("GET", "/demo"),
        ]
    if target.project == "lamp-cloud":
        return [
            ("GET", "/anyTenant/captcha"),
            ("GET", "/anyTenant/checkCaptcha"),
            ("GET", "/anyone/visible/resource"),
            ("GET", "/anyone/getPermissionList"),
        ]
    return []


def wait_for_port(host: str, port: int, timeout_s: int) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def container_running(name: str) -> bool:
    res = run(["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=10)
    return res.returncode == 0 and res.stdout.strip().lower() == "true"


def wait_for_http_ready(base_url: str, candidates: list[tuple[str, str]], container: str, timeout_s: int) -> bool:
    deadline = time.time() + timeout_s
    readiness_paths = [("GET", "/actuator/health"), ("GET", "/health"), ("GET", "/")] + candidates[:5]
    while time.time() < deadline:
        if not container_running(container):
            return False
        for method, path in readiness_paths:
            status, _, _, error = http_request(base_url, method, path, trace=False)
            if status is not None or not error:
                return True
        time.sleep(1)
    return False


def http_request(base_url: str, method: str, path: str, trace: bool) -> tuple[int | None, dict[str, str], bytes, str]:
    headers = {"User-Agent": "trace-validator/1.0"}
    data = None
    if trace:
        headers["X-Return-Trace"] = "true"
    if method == "POST":
        headers["Content-Type"] = "application/json"
        data = b"{}"
    req = urllib.request.Request(base_url + path, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, dict(resp.headers.items()), resp.read(8192), ""
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read(8192), ""
    except Exception as ex:
        return None, {}, b"", str(ex)


def decode_trace_header(header: str | None) -> tuple[bool, int, str, str]:
    if not header:
        return False, 0, "", "missing header"
    try:
        spans = json.loads(base64.b64decode(header))
        if not isinstance(spans, list) or not spans:
            return True, 0, "", "decoded but spans empty"
        valid = True
        for span in spans:
            if not span.get("src_file") or int(span.get("line_number") or 0) <= 0:
                valid = False
        first = spans[0]
        label = f"{first.get('content')} {first.get('src_file')}:{first.get('line_number')}"
        return valid, len(spans), label, "" if valid else "span missing src_file or positive line_number"
    except Exception as ex:
        return False, 0, "", f"decode failed: {ex}"


def docker_logs(name: str, lines: int = 80) -> str:
    res = run(["docker", "logs", "--tail", str(lines), name], timeout=10)
    return res.stdout[-6000:]


def validation_profiles(target: Target) -> str:
    # SpringBlade's launcher treats comma-separated profiles as multiple
    # environments and aborts; its own Dockerfiles use the single "test" env.
    if target.project == "SpringBlade":
        return "test"
    return "trace-validation,local,dev"


def real_dep_common_args() -> list[str]:
    return [
        "--spring.datasource.username=root",
        "--spring.datasource.password=123456",
        "--spring.datasource.hikari.initialization-fail-timeout=0",
        "--spring.datasource.druid.initial-size=0",
        "--spring.datasource.druid.min-idle=0",
        "--spring.datasource.druid.max-active=2",
        "--spring.data.redis.host=127.0.0.1",
        "--spring.data.redis.port=6379",
        "--spring.data.redis.password=password",
        "--spring.redis.host=127.0.0.1",
        "--spring.redis.port=6379",
        "--spring.redis.password=password",
        "--spring.rabbitmq.host=127.0.0.1",
        "--spring.rabbitmq.port=5672",
        "--spring.rabbitmq.username=guest",
        "--spring.rabbitmq.password=guest",
        "--spring.data.mongodb.uri=mongodb://127.0.0.1:27017/test?serverSelectionTimeoutMS=1000&connectTimeoutMS=1000",
        "--spring.cloud.nacos.server-addr=127.0.0.1:8848",
        "--spring.cloud.nacos.discovery.server-addr=127.0.0.1:8848",
        "--spring.cloud.nacos.config.server-addr=127.0.0.1:8848",
    ]


def target_extra_args(target: Target, real_deps: bool = False) -> list[str]:
    extras: list[str] = []
    extras.extend([
        f"--spring.application.name={target.module.split('/')[-1] if target.module else target.project}",
    ])
    if target.project == "RuoYi-Cloud-Plus":
        extras.extend([
            "--ruoyi.name=trace-validation",
            "--ruoyi.version=trace-validation",
            "--dubbo.consumer.check=false",
            "--dubbo.registry.check=false",
            "--dubbo.reference.check=false",
            "--dubbo.application.qos-enable=false",
        ])
        if real_deps:
            extras.extend([
                "--spring.datasource.dynamic.datasource.master.password=123456",
                "--spring.datasource.dynamic.datasource.slave.password=123456",
            ])
    elif target.project == "yudao-cloud":
        extras.extend([
            "--spring.main.allow-circular-references=true",
            "--spring.autoconfigure.exclude=com.alibaba.druid.spring.boot.autoconfigure.DruidDataSourceAutoConfigure",
            "--springdoc.api-docs.enabled=false",
            "--springdoc.swagger-ui.enabled=false",
            "--knife4j.enable=false",
            "--yudao.info.base-package=cn.iocoder.yudao",
            "--yudao.info.version=trace-validation",
            "--yudao.web.admin-ui.url=http://127.0.0.1:8080",
            "--yudao.api-encrypt.enable=false",
            "--yudao.api-encrypt.algorithm=AES",
            "--yudao.api-encrypt.request-key=52549111389893486934626385991395",
            "--yudao.api-encrypt.response-key=96103715984234343991809655248883",
            "--yudao.sms-code.expire-times=10m",
            "--yudao.sms-code.send-frequency=1m",
            "--yudao.sms-code.send-maximum-quantity-per-day=10",
            "--yudao.sms-code.begin-code=9999",
            "--yudao.sms-code.end-code=9999",
            "--spring.application.name=yudao-module-system-server",
        ])
        if real_deps:
            extras.extend([
                "--spring.datasource.dynamic.primary=master",
                "--spring.datasource.dynamic.datasource.master.url=jdbc:mysql://127.0.0.1:3306/ruoyi-vue-pro?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true&nullCatalogMeansCurrent=true&rewriteBatchedStatements=true",
                "--spring.datasource.dynamic.datasource.master.username=root",
                "--spring.datasource.dynamic.datasource.master.password=123456",
                "--spring.datasource.dynamic.datasource.master.driver-class-name=com.mysql.cj.jdbc.Driver",
                "--spring.datasource.dynamic.datasource.slave.lazy=true",
                "--spring.datasource.dynamic.datasource.slave.url=jdbc:mysql://127.0.0.1:3306/ruoyi-vue-pro?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true&nullCatalogMeansCurrent=true&rewriteBatchedStatements=true",
                "--spring.datasource.dynamic.datasource.slave.username=root",
                "--spring.datasource.dynamic.datasource.slave.password=123456",
                "--spring.datasource.dynamic.datasource.slave.driver-class-name=com.mysql.cj.jdbc.Driver",
                "--rocketmq.name-server=127.0.0.1:9876",
            ])
    elif target.project == "youlai-mall":
        extras.extend([
            "--spring.security.oauth2.authorizationserver.token-uri=http://127.0.0.1:8080/oauth2/token",
            "--wx.miniapp.app-id=trace-validation",
            "--wx.miniapp.app-secret=trace-validation",
            "--captcha.type=line",
            "--captcha.width=120",
            "--captcha.height=40",
            "--captcha.interfere-count=2",
            "--captcha.text-alpha=0.8",
            "--captcha.expire-seconds=120",
            "--captcha.code.type=random",
            "--captcha.code.length=4",
            "--captcha.font.name=SansSerif",
            "--captcha.font.weight=0",
            "--captcha.font.size=32",
            "--spring.datasource.url=jdbc:mysql://127.0.0.1:3306/oauth2_server?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true&nullCatalogMeansCurrent=true",
            "--springdoc.info.title=trace-validation",
            "--springdoc.info.version=trace-validation",
            "--springdoc.info.description=trace-validation",
            "--springdoc.info.contact.name=trace-validation",
            "--springdoc.info.contact.url=http://127.0.0.1",
            "--springdoc.info.contact.email=trace@example.invalid",
            "--springdoc.info.license.name=trace-validation",
            "--springdoc.info.license.url=http://127.0.0.1",
        ])
    elif target.project == "zlt-microservices-platform":
        zlt_password = "123456" if real_deps else ""
        extras.extend([
            "--zlt.datasource.ip=127.0.0.1",
            "--zlt.datasource.username=root",
            f"--zlt.datasource.password={zlt_password}",
            "--spring.datasource.url=jdbc:mysql://127.0.0.1:3306/oauth-center?useSSL=false&serverTimezone=UTC&allowPublicKeyRetrieval=true",
            "--spring.datasource.driver-class-name=com.mysql.cj.jdbc.Driver",
        ])
        if not real_deps:
            extras.extend([
                "--spring.datasource.username=root",
                f"--spring.datasource.password={zlt_password}",
            ])
    elif target.project == "MoGuBlog":
        extras.extend([
            "--spring.datasource.initialSize=0",
            "--spring.datasource.minIdle=0",
            "--spring.datasource.maxActive=1",
            "--spring.datasource.maxWait=60000",
            "--spring.datasource.timeBetweenEvictionRunsMillis=60000",
            "--spring.datasource.minEvictableIdleTimeMillis=300000",
            "--spring.datasource.validationQuery=SELECT 1",
            "--spring.datasource.testWhileIdle=true",
            "--spring.datasource.testOnBorrow=false",
            "--spring.datasource.testOnReturn=false",
            "--spring.datasource.poolPreparedStatements=false",
            "--spring.datasource.filters=stat",
            "--spring.datasource.maxPoolPreparedStatementPerConnectionSize=1",
            "--spring.datasource.useGlobalDataSourceStat=true",
            "--spring.datasource.connectionProperties=druid.stat.mergeSql=true;druid.stat.slowSqlMillis=500",
                "--tokenHead=Bearer ",
            "--tokenHeader=Authorization",
            "--audience.clientId=trace-validation",
            "--audience.base64Secret=dHJhY2UtdmFsaWRhdGlvbi1zZWNyZXQ=",
            "--audience.name=trace-validation",
            "--audience.expiresSecond=3600",
            "--audience.refreshSecond=7200",
            "--isRememberMeExpiresSecond=7200",
        ])
    elif target.project == "Cloud-Platform":
        extras.extend([
            "--jwt.expire=3600",
            "--jwt.rsa-secret=trace-validation",
            "--jwt.token-header=Authorization",
            "--jwt.pri-key=trace-validation",
            "--jwt.pub-key=trace-validation",
        ])
        if real_deps:
            extras.extend([
                "--spring.datasource.url=jdbc:mysql://127.0.0.1:3306/cloud_admin_v1?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true",
                "--spring.datasource.username=root",
                "--spring.datasource.password=123456",
                "--eureka.client.service-url.defaultZone=http://127.0.0.1:8761/eureka/",
            ])
    elif target.project == "lamp-cloud":
        extras.extend([
            "--knife4j.enable=false",
            "--knife4j.enabled=false",
        ])
        if real_deps:
            extras.extend([
                "--spring.datasource.url=jdbc:mysql://127.0.0.1:3306/lamp_none?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true",
            ])
    elif target.project == "Apollo" and real_deps:
        extras.extend([
            "--spring.datasource.url=jdbc:mysql://127.0.0.1:3306/ApolloConfigDB?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true",
            "--spring.datasource.driver-class-name=com.mysql.cj.jdbc.Driver",
            "--eureka.instance.hostname=127.0.0.1",
            "--eureka.client.service-url.defaultZone=http://127.0.0.1:8761/eureka/",
        ])
    elif target.project == "novel-cloud" and real_deps:
        extras.extend([
            "--spring.datasource.url=jdbc:mysql://127.0.0.1:3306/novel?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true",
        ])
    elif target.project == "PiggyMetrics" and real_deps:
        extras.extend([
            "--spring.cloud.config.fail-fast=false",
            "--management.health.rabbit.enabled=false",
            "--management.health.eureka.enabled=false",
            "--security.oauth2.resource.user-info-uri=http://127.0.0.1:5000/uaa/users/current",
            "--security.oauth2.client.client-id=account-service",
            "--security.oauth2.client.client-secret=password",
            "--security.oauth2.client.access-token-uri=http://127.0.0.1:5000/uaa/oauth/token",
        ])
    elif target.project == "MoGuBlog" and real_deps:
        extras.extend([
            "--spring.datasource.url=jdbc:mysql://127.0.0.1:3306/mogu_blog?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true",
            "--spring.datasource.username=root",
            "--spring.datasource.password=123456",
        ])
    return extras


def start_real_deps(args: argparse.Namespace) -> bool:
    if not REAL_DEPS_COMPOSE.exists():
        print(f"Real dependency compose not found: {REAL_DEPS_COMPOSE}", file=sys.stderr)
        return False
    services: list[str] = []
    if args.real_deps_services.strip().lower() != "all":
        services = [s.strip() for s in args.real_deps_services.split(",") if s.strip()]
    cmd = ["docker", "compose", "-f", str(REAL_DEPS_COMPOSE), "up", "-d", "--wait"] + services
    print("Starting real dependency stack: " + (", ".join(services) if services else "all services"), flush=True)
    res = run(cmd, timeout=args.real_deps_timeout)
    if res.returncode != 0:
        print(res.stdout[-4000:], file=sys.stderr)
        return False
    return True


def stop_real_deps() -> None:
    run(["docker", "compose", "-f", str(REAL_DEPS_COMPOSE), "down", "--remove-orphans"], timeout=180)


def target_real_compose(target: Target) -> Path | None:
    compose = EXAMPLES_YML / target.project / "compose.real.yaml"
    if target.project == "RuoYi-Cloud-Plus" and compose.exists():
        return compose
    return None


def validate_compose_real_target(target: Target, args: argparse.Namespace) -> TargetResult:
    compose = target_real_compose(target)
    cname = f"trace-real-{target.project.lower()}-{target.module.split('/')[-1].lower()}"
    result = TargetResult(target=target.name, image=target.image, container=cname, host_port=8080)
    if compose is None:
        result.state = "missing_real_compose"
        return result

    candidates = discover_candidates(target.module_dir, args.max_candidates, include_smoke=False)
    preferred = target_preferred_candidates(target)
    candidates = (preferred + [item for item in candidates if item not in preferred])[:args.max_candidates]
    result.candidates = len(candidates)

    up = run([
        "docker", "compose", "-f", str(compose), "up", "-d", "--wait", "--force-recreate", "--no-deps",
        "nacos", "ruoyi-system", "app",
    ], timeout=args.real_deps_timeout)
    if up.returncode != 0:
        result.state = "compose_up_failed"
        result.notes.append(up.stdout.strip()[-1000:])
        return result

    try:
        base_url = "http://127.0.0.1:8080"
        if not wait_for_port("127.0.0.1", 8080, args.startup_timeout):
            result.state = "port_not_ready"
            result.logs_tail = docker_logs(cname)
            return result
        time.sleep(args.ready_grace)
        if not wait_for_http_ready(base_url, candidates, cname, args.startup_timeout):
            result.state = "http_not_ready"
            result.logs_tail = docker_logs(cname)
            return result
        result.state = "running"
        for method, path in candidates:
            status, headers, _, error = http_request(base_url, method, path, trace=True)
            trace_header = None
            for key, value in headers.items():
                if key.lower() == "x-execution-trace":
                    trace_header = value
                    break
            valid, span_count, first_span, decode_error = decode_trace_header(trace_header)
            probe = ProbeResult(method=method, path=path, status=status,
                                trace_present=bool(trace_header), span_count=span_count,
                                valid=valid, error=error or decode_error, first_span=first_span)
            if valid:
                neg_status, neg_headers, _, _ = http_request(base_url, method, path, trace=False)
                result.negative_ok = not any(k.lower() == "x-execution-trace" for k in neg_headers)
                if not result.negative_ok:
                    probe.error = f"negative control returned trace header, status={neg_status}"
                result.probe = probe
                result.success = result.negative_ok
                return result
            if result.probe is None or (probe.trace_present and not result.probe.trace_present):
                result.probe = probe

        result.state = "no_valid_trace"
        result.logs_tail = docker_logs(cname)
        return result
    finally:
        if not args.keep_containers:
            run(["docker", "compose", "-f", str(compose), "rm", "-sf", "app"], timeout=60)
        if not args.keep_deps:
            run(["docker", "compose", "-f", str(compose), "rm", "-sf", "ruoyi-system", "nacos"], timeout=60)


def validate_target(target: Target, args: argparse.Namespace) -> TargetResult:
    if args.real_deps and target_real_compose(target) is not None:
        return validate_compose_real_target(target, args)

    cname = sanitize_container_name(target.project + "-" + (target.module or "root"))
    result = TargetResult(target=target.name, image=target.image, container=cname)
    candidates = discover_candidates(target.module_dir, args.max_candidates, include_smoke=args.smoke_mode)
    preferred = target_preferred_candidates(target)
    candidates = preferred + [item for item in candidates if item not in preferred]
    candidates = candidates[:args.max_candidates]
    result.candidates = len(candidates)
    profiles = "trace-smoke" if args.smoke_mode else validation_profiles(target)

    run(["docker", "rm", "-f", cname], timeout=20)
    mysql_password = "123456" if args.real_deps else ""
    envs = [
        "SERVER_PORT=8080",
        "SERVER_SERVLET_CONTEXT_PATH=",
        f"SPRING_PROFILES_ACTIVE={profiles}",
        "SPRING_MAIN_LAZY_INITIALIZATION=true",
        "SPRING_MAIN_ALLOW_BEAN_DEFINITION_OVERRIDING=true",
        "SPRING_CLOUD_BOOTSTRAP_ENABLED=false",
        "SPRING_CONFIG_LOCATION=optional:file:/tmp/trace-validation.yml",
        "SPRING_CONFIG_IMPORT=",
        "SPRING_CLOUD_CONFIG_ENABLED=false",
        "SPRING_CLOUD_CONFIG_IMPORT_CHECK_ENABLED=false",
        "SPRING_CLOUD_NACOS_CONFIG_ENABLED=false",
        "SPRING_CLOUD_NACOS_DISCOVERY_ENABLED=false",
        "SPRING_CLOUD_NACOS_CONFIG_IMPORT_CHECK_ENABLED=false",
        "NACOS_CONFIG_ENABLED=false",
        "NACOS_DISCOVERY_ENABLED=false",
        "NACOS_CORE_AUTH_ENABLED=false",
        "SPRING_CLOUD_CONSUL_ENABLED=false",
        "EUREKA_CLIENT_ENABLED=false",
        "EUREKA_CLIENT_REGISTER_WITH_EUREKA=false",
        "EUREKA_CLIENT_FETCH_REGISTRY=false",
        "SPRING_CLOUD_DISCOVERY_ENABLED=false",
        "SPRING_CLOUD_SERVICE_REGISTRY_AUTO_REGISTRATION_ENABLED=false",
        "SEATA_ENABLED=false",
        "SEATA_ENABLE_AUTO_DATA_SOURCE_PROXY=false",
        "DUBBO_APPLICATION_REGISTER_MODE=instance",
        "DUBBO_REGISTRY_ADDRESS=N/A",
        "DUBBO_CONFIG_CENTER_ADDRESS=N/A",
        "DUBBO_METADATA_REPORT_ADDRESS=N/A",
        "SPRING_DATASOURCE_URL=jdbc:mysql://127.0.0.1:3306/test?useSSL=false&serverTimezone=UTC&allowPublicKeyRetrieval=true",
        "SPRING_DATASOURCE_DRIVER_CLASS_NAME=com.mysql.cj.jdbc.Driver",
        "SPRING_DATASOURCE_USERNAME=root",
        f"SPRING_DATASOURCE_PASSWORD={mysql_password}",
        "SPRING_DATASOURCE_HIKARI_INITIALIZATION_FAIL_TIMEOUT=0",
        "SPRING_DATASOURCE_DRUID_INITIAL_SIZE=0",
        "SPRING_DATA_MONGODB_URI=mongodb://127.0.0.1:27017/test",
        "SPRING_RABBITMQ_HOST=127.0.0.1",
        "SPRING_RABBITMQ_PORT=5672",
        "SPRING_DATA_REDIS_PASSWORD=password" if args.real_deps else "SPRING_DATA_REDIS_PASSWORD=",
        "SPRING_REDIS_HOST=127.0.0.1",
        "SPRING_REDIS_PORT=6379",
        "SPRING_REDIS_PASSWORD=password" if args.real_deps else "SPRING_REDIS_PASSWORD=",
        "OTEL_SDK_DISABLED=true",
        "OTEL_INSTRUMENTATION_COMMON_DEFAULT_ENABLED=false",
        "MANAGEMENT_ENDPOINT_HEALTH_PROBES_ENABLED=true",
        "MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE=health,info",
    ]
    if target.project == "youlai-mall":
        envs.append("JAVA_OPTS=--add-opens java.base/java.io=ALL-UNNAMED --add-opens java.base/java.util.concurrent.locks=ALL-UNNAMED")
    if args.smoke_mode:
        envs.extend([
            "TRACE_SMOKE_MODE=true",
            "SPRING_PROFILES_ACTIVE=trace-smoke",
        ])
    cmd = ["docker", "run", "-d", "--name", cname]
    for env in envs:
        cmd.extend(["-e", env])
    if args.host_network:
        cmd.extend(["--network", "host", target.image])
    else:
        cmd.extend(["-p", "127.0.0.1::8080", target.image])
    if args.smoke_mode:
        cmd.extend(["--server.port=8080"])
    else:
        cmd.extend([
            "--spring.config.location=optional:file:/tmp/trace-validation.yml",
            "--spring.cloud.bootstrap.enabled=false",
            "--spring.cloud.nacos.config.enabled=false",
            "--spring.cloud.nacos.discovery.enabled=false",
            "--spring.cloud.nacos.config.import-check.enabled=false",
            "--spring.cloud.config.enabled=false",
            "--spring.cloud.config.import-check.enabled=false",
            "--spring.cloud.discovery.enabled=false",
            "--spring.cloud.service-registry.auto-registration.enabled=false",
            "--eureka.client.enabled=false",
            "--seata.enabled=false",
            "--spring.main.allow-bean-definition-overriding=true",
            f"--spring.profiles.active={profiles}",
            "--server.port=8080",
        ])
        if args.real_deps:
            cmd.extend(real_dep_common_args())
        cmd.extend(target_extra_args(target, args.real_deps))
    started = run(cmd, timeout=60)
    if started.returncode != 0:
        result.state = "docker_run_failed"
        result.notes.append(started.stdout.strip()[-1000:])
        return result

    try:
        if args.host_network:
            result.host_port = 8080
        else:
            port_out = run(["docker", "port", cname, "8080/tcp"], timeout=10).stdout.strip().splitlines()
            if not port_out:
                result.state = "no_port_mapping"
                result.logs_tail = docker_logs(cname)
                return result
            result.host_port = int(port_out[-1].rsplit(":", 1)[-1])
        base_url = f"http://127.0.0.1:{result.host_port}"
        if not wait_for_port("127.0.0.1", result.host_port, args.startup_timeout):
            result.state = "port_not_ready"
            result.logs_tail = docker_logs(cname)
            return result

        # Give Spring a short grace window after the TCP port opens, then wait
        # until the HTTP stack actually accepts requests.
        time.sleep(args.ready_grace)
        if not wait_for_http_ready(base_url, candidates, cname, args.startup_timeout):
            result.state = "http_not_ready"
            result.logs_tail = docker_logs(cname)
            return result
        result.state = "running"
        for method, path in candidates:
            status, headers, _, error = http_request(base_url, method, path, trace=True)
            trace_header = None
            for key, value in headers.items():
                if key.lower() == "x-execution-trace":
                    trace_header = value
                    break
            valid, span_count, first_span, decode_error = decode_trace_header(trace_header)
            probe = ProbeResult(method=method, path=path, status=status,
                                trace_present=bool(trace_header), span_count=span_count,
                                valid=valid, error=error or decode_error, first_span=first_span)
            if valid:
                neg_status, neg_headers, _, _ = http_request(base_url, method, path, trace=False)
                result.negative_ok = not any(k.lower() == "x-execution-trace" for k in neg_headers)
                if not result.negative_ok:
                    probe.error = f"negative control returned trace header, status={neg_status}"
                result.probe = probe
                result.success = result.negative_ok
                return result
            if result.probe is None or (probe.trace_present and not result.probe.trace_present):
                result.probe = probe

        result.state = "no_valid_trace"
        result.logs_tail = docker_logs(cname)
        return result
    finally:
        if not args.keep_containers:
            run(["docker", "rm", "-f", cname], timeout=20)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", help="Project name or image substring to validate; default: all targets")
    parser.add_argument("--startup-timeout", type=int, default=45)
    parser.add_argument("--ready-grace", type=int, default=3)
    parser.add_argument("--max-candidates", type=int, default=80)
    parser.add_argument("--keep-containers", action="store_true")
    parser.add_argument("--host-network", action="store_true",
                        help="Run application containers with Docker host networking; useful for localhost sidecar deps")
    parser.add_argument("--real-deps", action="store_true",
                        help="Use examples-yml/_shared/real-deps.compose.yaml sidecars and host networking for real dependency validation")
    parser.add_argument("--real-deps-services", default="mysql,redis",
                        help="Comma-separated sidecars to start with --real-deps, or 'all'. Default: mysql,redis")
    parser.add_argument("--real-deps-timeout", type=int, default=900,
                        help="Timeout in seconds for starting the real dependency stack")
    parser.add_argument("--keep-deps", action="store_true",
                        help="Do not docker compose down the real dependency stack after validation")
    parser.add_argument("--smoke-mode", action="store_true",
                        help="Run images with TRACE_SMOKE_MODE=true and validate /__trace_smoke")
    parser.add_argument("--json-out", default=str(ROOT / "logs" / "trace_validation" / "results.json"))
    return parser.parse_args(list(argv))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    if not docker_available():
        print("Docker is not available", file=sys.stderr)
        return 2

    selected = TARGETS
    if args.targets:
        needles = [x.lower() for x in args.targets]
        selected = [t for t in TARGETS if any(n in t.name.lower() or n in t.image.lower() for n in needles)]
    if not selected:
        print("No targets selected", file=sys.stderr)
        return 2

    deps_started = False
    if args.real_deps:
        args.host_network = True
        deps_started = start_real_deps(args)
        if not deps_started:
            return 2

    output_path = Path(args.json_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[TargetResult] = []
    try:
        for target in selected:
            print(f"\n=== {target.name} ===", flush=True)
            result = validate_target(target, args)
            results.append(result)
            probe = result.probe
            if result.success and probe:
                print(f"✅ {probe.method} {probe.path} HTTP {probe.status} spans={probe.span_count} first={probe.first_span}", flush=True)
            else:
                detail = f"{probe.method} {probe.path} HTTP {probe.status} {probe.error}" if probe else "; ".join(result.notes)
                print(f"❌ state={result.state} candidates={result.candidates} detail={detail}", flush=True)

        serializable = [r.__dict__ | {"probe": r.probe.__dict__ if r.probe else None} for r in results]
        output_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2))
        ok = sum(1 for r in results if r.success)
        print(f"\nSummary: {ok}/{len(results)} targets passed")
        print(f"JSON report: {output_path}")
        return 0 if ok == len(results) else 1
    finally:
        if deps_started and not args.keep_deps:
            stop_real_deps()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))