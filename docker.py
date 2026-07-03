"""
docker.py — Docker Compose 容器管理中心

通过 docker compose 管理 examples-yml/ 下各项目的生命周期,
提供 start / stop / restart / status 等操作。
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════

class ContainerState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class InstanceInfo:
    """描述一个已启动/已管理的项目实例."""
    project_name: str           # 项目名, e.g. "Apollo"
    container_name: str         # 容器名, e.g. "trace-real-apollo-apollo-adminservice"
    port: int                   # 服务端口 (默认 8080)
    state: ContainerState       # 当前容器状态
    endpoint: str = ""          # 测试端点 (来自 labels)
    compose_file: str = ""      # compose 文件路径
    image: str = ""             # 镜像名
    started_at: Optional[float] = None  # 启动时间戳 (time.time())


# ═══════════════════════════════════════════════════════════════════════
# Docker 容器管理中心
# ═══════════════════════════════════════════════════════════════════════

class Docker:
    """
    Docker Compose 容器管理中心.

    管理 examples-yml/ 下各项目的容器生命周期:
      - start: 启动项目 (docker compose up -d)
      - stop: 停止项目 (docker compose stop + rm)
      - restart: 重启项目
      - status: 查询容器状态
      - wait_ready: 等待服务就绪 (HTTP 健康检查)
      - list_projects: 列出所有可管理的项目
    """

    DEFAULT_PORT = 8080
    DEFAULT_STARTUP_TIMEOUT = 120  # 秒
    HEALTH_CHECK_INTERVAL = 3     # 秒

    def __init__(self, base_dir: Optional[str | Path] = None):
        """
        Args:
            base_dir: aiops-py 项目根目录, 默认自动检测
        """
        if base_dir is None:
            # 假设此文件在 aiops-py 根目录
            base_dir = Path(__file__).parent
        self._base_dir = Path(base_dir)
        self._yml_dir = self._base_dir / "examples-yml"

        # 当前已管理的实例状态缓存
        self._instances: dict[str, InstanceInfo] = {}

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @property
    def yml_dir(self) -> Path:
        return self._yml_dir

    # ── 项目发现 ─────────────────────────────────────────────────

    def list_projects(self) -> list[str]:
        """列出所有可管理的项目名 (有 compose.real.yaml 的目录)."""
        projects = []
        if not self._yml_dir.exists():
            return projects
        for d in sorted(self._yml_dir.iterdir()):
            if d.is_dir() and (d / "compose.real.yaml").exists():
                projects.append(d.name)
        return projects

    def get_compose_file(self, project_name: str) -> Path:
        """获取项目的 compose 文件路径."""
        return self._yml_dir / project_name / "compose.real.yaml"

    # ── 核心生命周期 ─────────────────────────────────────────────

    def start(self, project_name: str, timeout: int = DEFAULT_STARTUP_TIMEOUT) -> InstanceInfo:
        """
        启动项目容器.

        Args:
            project_name: 项目名称 (例如 "Apollo", "java-microservice")
            timeout: 等待容器启动的超时时间 (秒)

        Returns:
            InstanceInfo 描述启动后的实例

        Raises:
            FileNotFoundError: compose 文件不存在
            RuntimeError: docker compose up 失败
        """
        compose_file = self.get_compose_file(project_name)
        if not compose_file.exists():
            raise FileNotFoundError(
                f"Compose file not found: {compose_file}"
            )

        # docker compose up -d
        result = self._run_compose(compose_file, ["up", "-d", "--pull", "never"])
        if result.returncode != 0:
            # docker compose up 即使成功也可能有 stderr 输出
            # 只在确实失败时 raise
            if "error" in result.stderr.lower() and result.returncode != 0:
                raise RuntimeError(
                    f"Failed to start {project_name}: {result.stderr}"
                )

        # 解析实例信息
        info = self._build_instance_info(project_name, compose_file)
        info.state = ContainerState.STARTING
        info.started_at = time.time()
        self._instances[project_name] = info

        # 检查容器是否真的在运行
        actual_state = self._inspect_container_state(info.container_name)
        info.state = actual_state

        return info

    def stop(self, project_name: str) -> bool:
        """
        停止项目容器 (仅停止 app 服务, 保留基础设施).

        Args:
            project_name: 项目名称

        Returns:
            True 表示停止成功
        """
        compose_file = self.get_compose_file(project_name)
        if not compose_file.exists():
            return False

        # 先 stop 再 rm
        self._run_compose(compose_file, ["stop", "app"])
        self._run_compose(compose_file, ["rm", "-f", "app"])

        if project_name in self._instances:
            self._instances[project_name].state = ContainerState.STOPPED

        return True

    def restart(self, project_name: str, timeout: int = DEFAULT_STARTUP_TIMEOUT) -> InstanceInfo:
        """
        重启项目容器 (先 stop 再 start).

        Returns:
            新的 InstanceInfo
        """
        self.stop(project_name)
        time.sleep(2)
        return self.start(project_name, timeout)

    def stop_all(self) -> None:
        """停止所有已管理的项目容器."""
        for project_name in list(self._instances.keys()):
            self.stop(project_name)

    # ── 状态查询 ─────────────────────────────────────────────────

    def status(self, project_name: str) -> InstanceInfo:
        """
        查询项目容器当前状态.

        Returns:
            最新的 InstanceInfo (会刷新 state)
        """
        compose_file = self.get_compose_file(project_name)
        if not compose_file.exists():
            raise FileNotFoundError(f"Compose file not found: {compose_file}")

        info = self._build_instance_info(project_name, compose_file)
        info.state = self._inspect_container_state(info.container_name)

        # 如果之前有启动记录, 保留 started_at
        if project_name in self._instances:
            info.started_at = self._instances[project_name].started_at

        self._instances[project_name] = info
        return info

    def wait_ready(
        self,
        project_name: str,
        timeout: int = DEFAULT_STARTUP_TIMEOUT,
        check_interval: int = HEALTH_CHECK_INTERVAL,
    ) -> bool:
        """
        等待项目的 HTTP 服务就绪.

        通过向 localhost:port 发送请求检测, 任意 HTTP 响应即视为就绪.

        Returns:
            True 表示服务已就绪, False 表示超时
        """
        info = self._instances.get(project_name)
        if info is None:
            info = self.status(project_name)

        import urllib.request
        import urllib.error

        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{info.port}{info.endpoint or '/'}"

        while time.time() < deadline:
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    # 任何 HTTP 响应都算就绪
                    info.state = ContainerState.RUNNING
                    return True
            except urllib.error.HTTPError:
                # 4xx/5xx 也算服务已启动
                info.state = ContainerState.RUNNING
                return True
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(check_interval)

        # 超时
        info.state = self._inspect_container_state(info.container_name)
        return False

    def is_running(self, project_name: str) -> bool:
        """快速检查容器是否在运行."""
        info = self._instances.get(project_name)
        container_name = info.container_name if info else self._guess_container_name(project_name)
        return self._inspect_container_state(container_name) == ContainerState.RUNNING

    # ── 内部方法 ─────────────────────────────────────────────────

    def _run_compose(
        self, compose_file: Path, args: list[str], timeout: int = 60
    ) -> subprocess.CompletedProcess:
        """执行 docker compose 命令."""
        cmd = ["docker", "compose", "-f", str(compose_file)] + args
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(compose_file.parent),
        )

    def _build_instance_info(self, project_name: str, compose_file: Path) -> InstanceInfo:
        """从 compose 文件解析 InstanceInfo."""
        container_name = self._guess_container_name(project_name)
        endpoint = self._parse_endpoint_label(compose_file)
        image = self._parse_image(compose_file)

        return InstanceInfo(
            project_name=project_name,
            container_name=container_name,
            port=self.DEFAULT_PORT,
            state=ContainerState.UNKNOWN,
            endpoint=endpoint,
            compose_file=str(compose_file),
            image=image,
        )

    def _guess_container_name(self, project_name: str) -> str:
        """
        从 compose 文件中读取 container_name, 
        或者通过 docker compose ps 获取.
        """
        compose_file = self.get_compose_file(project_name)
        if compose_file.exists():
            # 简单解析 container_name 行
            for line in compose_file.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("container_name:"):
                    return stripped.split(":", 1)[1].strip()
        # fallback
        return f"trace-real-{project_name}"

    def _parse_endpoint_label(self, compose_file: Path) -> str:
        """解析 compose 文件中 aiops.trace.endpoint label."""
        for line in compose_file.read_text().splitlines():
            stripped = line.strip()
            if "aiops.trace.endpoint:" in stripped:
                return stripped.split("aiops.trace.endpoint:", 1)[1].strip()
        return "/"

    def _parse_image(self, compose_file: Path) -> str:
        """解析 compose 文件中的 image 字段."""
        for line in compose_file.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("image:"):
                return stripped.split(":", 1)[1].strip()
        return ""

    def _inspect_container_state(self, container_name: str) -> ContainerState:
        """通过 docker inspect 查询容器状态."""
        try:
            result = subprocess.run(
                [
                    "docker", "inspect",
                    "--format", "{{.State.Status}}",
                    container_name,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return ContainerState.STOPPED
            status = result.stdout.strip().lower()
            if status == "running":
                return ContainerState.RUNNING
            elif status in ("exited", "dead", "removed"):
                return ContainerState.STOPPED
            elif status in ("created", "restarting"):
                return ContainerState.STARTING
            else:
                return ContainerState.UNKNOWN
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ContainerState.UNKNOWN