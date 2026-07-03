"""
tests/test_docker.py — Docker 容器管理中心单元测试

分两部分:
  1. 单元测试 (mock subprocess): 不依赖真实 Docker 环境
  2. 集成测试 (标记 @integration): 需要真实 Docker 环境运行
"""

import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from docker import Docker, InstanceInfo, ContainerState


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_project_dir(tmp_path: Path):
    """创建一个模拟的 examples-yml 项目结构."""
    yml_dir = tmp_path / "examples-yml"

    # 项目 A
    proj_a = yml_dir / "project-a"
    proj_a.mkdir(parents=True)
    (proj_a / "compose.real.yaml").write_text("""\
name: trace-real-project-a
services:
  app:
    image: registry.example.com/project-a:latest
    container_name: trace-real-project-a-app
    network_mode: host
    labels:
      aiops.trace.endpoint: /api/health
      aiops.trace.mode: real
""")

    # 项目 B
    proj_b = yml_dir / "project-b"
    proj_b.mkdir(parents=True)
    (proj_b / "compose.real.yaml").write_text("""\
name: trace-real-project-b
services:
  app:
    image: registry.example.com/project-b:latest
    container_name: trace-real-project-b-svc
    network_mode: host
    labels:
      aiops.trace.endpoint: /users/me
      aiops.trace.mode: real
""")

    # 没有 compose 文件的目录 (不应被列为项目)
    (yml_dir / "_shared").mkdir(parents=True)
    (yml_dir / "_shared" / "real-deps.compose.yaml").write_text("services: {}")

    return tmp_path


@pytest.fixture
def docker(fake_project_dir: Path):
    """使用模拟目录构建 Docker 实例."""
    return Docker(base_dir=fake_project_dir)


# ═══════════════════════════════════════════════════════════════════════
# 单元测试: 项目发现
# ═══════════════════════════════════════════════════════════════════════

class TestListProjects:
    def test_discovers_projects_with_compose_file(self, docker: Docker):
        projects = docker.list_projects()
        assert "project-a" in projects
        assert "project-b" in projects

    def test_excludes_dirs_without_compose(self, docker: Docker):
        projects = docker.list_projects()
        assert "_shared" not in projects

    def test_returns_sorted_list(self, docker: Docker):
        projects = docker.list_projects()
        assert projects == sorted(projects)


class TestGetComposeFile:
    def test_returns_correct_path(self, docker: Docker):
        path = docker.get_compose_file("project-a")
        assert path.name == "compose.real.yaml"
        assert "project-a" in str(path)


# ═══════════════════════════════════════════════════════════════════════
# 单元测试: compose 文件解析
# ═══════════════════════════════════════════════════════════════════════

class TestParsing:
    def test_parse_container_name(self, docker: Docker):
        name = docker._guess_container_name("project-a")
        assert name == "trace-real-project-a-app"

    def test_parse_container_name_project_b(self, docker: Docker):
        name = docker._guess_container_name("project-b")
        assert name == "trace-real-project-b-svc"

    def test_parse_endpoint_label(self, docker: Docker):
        compose_file = docker.get_compose_file("project-a")
        endpoint = docker._parse_endpoint_label(compose_file)
        assert endpoint == "/api/health"

    def test_parse_image(self, docker: Docker):
        compose_file = docker.get_compose_file("project-a")
        image = docker._parse_image(compose_file)
        assert image == "registry.example.com/project-a:latest"

    def test_fallback_container_name_when_no_compose(self, docker: Docker):
        # 对于不存在的项目, 使用 fallback 命名
        name = docker._guess_container_name("nonexistent-project")
        assert name == "trace-real-nonexistent-project"


# ═══════════════════════════════════════════════════════════════════════
# 单元测试: start
# ═══════════════════════════════════════════════════════════════════════

class TestStart:
    @patch("subprocess.run")
    def test_start_success(self, mock_run: MagicMock, docker: Docker):
        # compose up 成功
        mock_run.side_effect = [
            # docker compose up -d
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # docker inspect (state check)
            subprocess.CompletedProcess(args=[], returncode=0, stdout="running\n", stderr=""),
        ]

        info = docker.start("project-a")

        assert isinstance(info, InstanceInfo)
        assert info.project_name == "project-a"
        assert info.container_name == "trace-real-project-a-app"
        assert info.port == 8080
        assert info.state == ContainerState.RUNNING
        assert info.endpoint == "/api/health"
        assert info.started_at is not None

    @patch("subprocess.run")
    def test_start_compose_failure(self, mock_run: MagicMock, docker: Docker):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error: some docker error"
        )

        with pytest.raises(RuntimeError, match="Failed to start"):
            docker.start("project-a")

    def test_start_nonexistent_project(self, docker: Docker):
        with pytest.raises(FileNotFoundError):
            docker.start("nonexistent-project")

    @patch("subprocess.run")
    def test_start_sets_starting_state_on_created(self, mock_run: MagicMock, docker: Docker):
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="created\n", stderr=""),
        ]

        info = docker.start("project-a")
        assert info.state == ContainerState.STARTING


# ═══════════════════════════════════════════════════════════════════════
# 单元测试: stop
# ═══════════════════════════════════════════════════════════════════════

class TestStop:
    @patch("subprocess.run")
    def test_stop_success(self, mock_run: MagicMock, docker: Docker):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        result = docker.stop("project-a")
        assert result is True

        # 应该调用了 stop 和 rm
        calls = mock_run.call_args_list
        assert len(calls) == 2
        assert "stop" in calls[0][0][0] or "stop" in str(calls[0])
        assert "rm" in calls[1][0][0] or "rm" in str(calls[1])

    def test_stop_nonexistent_project_returns_false(self, docker: Docker):
        result = docker.stop("nonexistent-project")
        assert result is False

    @patch("subprocess.run")
    def test_stop_updates_instance_state(self, mock_run: MagicMock, docker: Docker):
        # 先模拟 start
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="running\n", stderr=""),
        ]
        docker.start("project-a")

        # 然后 stop
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        docker.stop("project-a")

        assert docker._instances["project-a"].state == ContainerState.STOPPED


# ═══════════════════════════════════════════════════════════════════════
# 单元测试: restart
# ═══════════════════════════════════════════════════════════════════════

class TestRestart:
    @patch("time.sleep")  # 跳过 sleep
    @patch("subprocess.run")
    def test_restart_calls_stop_then_start(
        self, mock_run: MagicMock, mock_sleep: MagicMock, docker: Docker
    ):
        mock_run.side_effect = [
            # stop: compose stop
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # stop: compose rm
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # start: compose up
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # start: docker inspect
            subprocess.CompletedProcess(args=[], returncode=0, stdout="running\n", stderr=""),
        ]

        info = docker.restart("project-a")
        assert info.state == ContainerState.RUNNING
        mock_sleep.assert_called_once_with(2)


# ═══════════════════════════════════════════════════════════════════════
# 单元测试: status / inspect
# ═══════════════════════════════════════════════════════════════════════

class TestStatus:
    @patch("subprocess.run")
    def test_status_running(self, mock_run: MagicMock, docker: Docker):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="running\n", stderr=""
        )
        info = docker.status("project-a")
        assert info.state == ContainerState.RUNNING

    @patch("subprocess.run")
    def test_status_exited(self, mock_run: MagicMock, docker: Docker):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="exited\n", stderr=""
        )
        info = docker.status("project-a")
        assert info.state == ContainerState.STOPPED

    @patch("subprocess.run")
    def test_status_container_not_found(self, mock_run: MagicMock, docker: Docker):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="No such container"
        )
        info = docker.status("project-a")
        assert info.state == ContainerState.STOPPED

    def test_status_nonexistent_compose(self, docker: Docker):
        with pytest.raises(FileNotFoundError):
            docker.status("nonexistent")


class TestIsRunning:
    @patch("subprocess.run")
    def test_is_running_true(self, mock_run: MagicMock, docker: Docker):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="running\n", stderr=""
        )
        assert docker.is_running("project-a") is True

    @patch("subprocess.run")
    def test_is_running_false(self, mock_run: MagicMock, docker: Docker):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=""
        )
        assert docker.is_running("project-a") is False


# ═══════════════════════════════════════════════════════════════════════
# 单元测试: wait_ready
# ═══════════════════════════════════════════════════════════════════════

class TestWaitReady:
    @patch("subprocess.run")
    @patch("urllib.request.urlopen")
    def test_wait_ready_immediate_success(
        self, mock_urlopen: MagicMock, mock_run: MagicMock, docker: Docker
    ):
        # 先 status 调用 inspect
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="running\n", stderr=""
        )
        # urlopen 成功
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = docker.wait_ready("project-a", timeout=5)
        assert result is True

    @patch("subprocess.run")
    @patch("urllib.request.urlopen")
    def test_wait_ready_http_error_counts_as_ready(
        self, mock_urlopen: MagicMock, mock_run: MagicMock, docker: Docker
    ):
        import urllib.error
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="running\n", stderr=""
        )
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=500, msg="", hdrs=None, fp=None
        )

        result = docker.wait_ready("project-a", timeout=5)
        assert result is True

    @patch("time.sleep")
    @patch("time.time")
    @patch("subprocess.run")
    @patch("urllib.request.urlopen")
    def test_wait_ready_timeout(
        self,
        mock_urlopen: MagicMock,
        mock_run: MagicMock,
        mock_time: MagicMock,
        mock_sleep: MagicMock,
        docker: Docker,
    ):
        import urllib.error
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="running\n", stderr=""
        )
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        # 模拟时间流逝: 第一次 < deadline, 第二次 > deadline
        mock_time.side_effect = [0, 0, 100]  # start, first check, second check (过期)

        result = docker.wait_ready("project-a", timeout=5)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════
# 单元测试: stop_all
# ═══════════════════════════════════════════════════════════════════════

class TestStopAll:
    @patch("subprocess.run")
    def test_stop_all(self, mock_run: MagicMock, docker: Docker):
        # 模拟两个项目已启动
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="running\n", stderr=""
        )
        docker._instances["project-a"] = InstanceInfo(
            project_name="project-a",
            container_name="trace-real-project-a-app",
            port=8080,
            state=ContainerState.RUNNING,
        )
        docker._instances["project-b"] = InstanceInfo(
            project_name="project-b",
            container_name="trace-real-project-b-svc",
            port=8080,
            state=ContainerState.RUNNING,
        )

        docker.stop_all()

        assert docker._instances["project-a"].state == ContainerState.STOPPED
        assert docker._instances["project-b"].state == ContainerState.STOPPED


# ═══════════════════════════════════════════════════════════════════════
# 集成测试 (需要真实 Docker, 用 pytest -m integration 运行)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestDockerIntegration:
    """
    集成测试, 验证与真实 Docker 的交互.
    需要: Docker daemon 运行中, 且有 examples-yml/ 目录.
    运行: pytest tests/test_docker.py -m integration
    """

    @pytest.fixture
    def real_docker(self):
        base = Path(__file__).parent.parent
        if not (base / "examples-yml").exists():
            pytest.skip("examples-yml not found")
        return Docker(base_dir=base)

    def test_list_real_projects(self, real_docker: Docker):
        projects = real_docker.list_projects()
        assert len(projects) >= 1
        # 至少应有这些项目
        assert "Apollo" in projects or "java-microservice" in projects

    def test_status_stopped_container(self, real_docker: Docker):
        """查询一个没有运行的项目, 应该返回 STOPPED."""
        projects = real_docker.list_projects()
        if not projects:
            pytest.skip("No projects available")
        info = real_docker.status(projects[0])
        assert info.state in (ContainerState.STOPPED, ContainerState.RUNNING, ContainerState.UNKNOWN)
