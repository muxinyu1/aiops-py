"""Tests for api_discovery module."""

import textwrap
import tempfile
import os

import pytest

from api_discovery import discover_api_entries, _parse_java_file, _extract_entries_from_class
from expected_path import APIEntry


@pytest.fixture
def java_controller(tmp_path):
    """Create a temporary Java controller file for testing."""
    code = textwrap.dedent("""\
        package com.example.demo.controller;

        import org.springframework.web.bind.annotation.*;

        @RestController
        @RequestMapping("/api/users")
        public class UserController {

            @GetMapping("/{id}")
            public User getUser(@PathVariable Long id) {
                return userService.findById(id);
            }

            @PostMapping
            public User createUser(@RequestBody UserDTO dto) {
                return userService.create(dto);
            }

            @PutMapping("/{id}")
            public User updateUser(@PathVariable Long id, @RequestBody UserDTO dto) {
                return userService.update(id, dto);
            }

            @DeleteMapping("/{id}")
            public void deleteUser(@PathVariable Long id) {
                userService.delete(id);
            }

            // Not an endpoint - private method
            private void validateUser(UserDTO dto) {
                // ...
            }
        }
    """)
    filepath = tmp_path / "src" / "main" / "java" / "com" / "example" / "demo" / "controller" / "UserController.java"
    filepath.parent.mkdir(parents=True)
    filepath.write_text(code)
    return tmp_path


@pytest.fixture
def legacy_controller(tmp_path):
    """Create a legacy-style @Controller + @RequestMapping(method=...) file."""
    code = textwrap.dedent("""\
        package com.example.legacy.controller;

        import org.springframework.stereotype.Controller;
        import org.springframework.web.bind.annotation.*;

        @Controller
        @RequestMapping("/admin")
        public class AdminController {

            @ResponseBody
            @RequestMapping(value = "/login", method = RequestMethod.POST)
            public Result login(@RequestBody LoginForm form) {
                return authService.login(form);
            }

            @ResponseBody
            @RequestMapping(value = "/info", method = RequestMethod.GET)
            public Result getInfo() {
                return authService.getCurrentInfo();
            }

            @ResponseBody
            @RequestMapping("/logout")
            public Result logout() {
                return authService.logout();
            }
        }
    """)
    filepath = tmp_path / "src" / "main" / "java" / "AdminController.java"
    filepath.parent.mkdir(parents=True)
    filepath.write_text(code)
    return tmp_path


@pytest.fixture
def no_class_path_controller(tmp_path):
    """Controller without class-level @RequestMapping."""
    code = textwrap.dedent("""\
        package com.example.app.controller;

        import org.springframework.web.bind.annotation.*;

        @RestController
        public class HealthController {

            @GetMapping("/health")
            public String health() {
                return "ok";
            }

            @GetMapping("/metrics")
            public Metrics metrics() {
                return metricsService.collect();
            }
        }
    """)
    filepath = tmp_path / "src" / "main" / "java" / "HealthController.java"
    filepath.parent.mkdir(parents=True)
    filepath.write_text(code)
    return tmp_path


class TestParseModernController:
    """Tests for modern Spring MVC annotation style."""

    def test_discover_get_post_put_delete(self, java_controller):
        entries = discover_api_entries(str(java_controller))
        assert len(entries) == 4

        methods = {e.http_method for e in entries}
        assert methods == {"GET", "POST", "PUT", "DELETE"}

    def test_path_composition(self, java_controller):
        entries = discover_api_entries(str(java_controller))
        by_method = {e.http_method: e for e in entries}

        assert by_method["GET"].http_path == "/api/users/{id}"
        assert by_method["POST"].http_path == "/api/users"
        assert by_method["PUT"].http_path == "/api/users/{id}"
        assert by_method["DELETE"].http_path == "/api/users/{id}"

    def test_class_and_method_names(self, java_controller):
        entries = discover_api_entries(str(java_controller))
        by_method = {e.http_method: e for e in entries}

        assert by_method["GET"].class_name == "com.example.demo.controller.UserController"
        assert by_method["GET"].method == "getUser"
        assert by_method["POST"].method == "createUser"

    def test_returns_api_entry_type(self, java_controller):
        entries = discover_api_entries(str(java_controller))
        assert all(isinstance(e, APIEntry) for e in entries)

    def test_line_numbers_positive(self, java_controller):
        entries = discover_api_entries(str(java_controller))
        assert all(e.line_number > 0 for e in entries)


class TestParseLegacyController:
    """Tests for legacy @Controller + @RequestMapping(method=...) style."""

    def test_discover_endpoints(self, legacy_controller):
        entries = discover_api_entries(str(legacy_controller))
        assert len(entries) == 3

    def test_method_extraction(self, legacy_controller):
        entries = discover_api_entries(str(legacy_controller))
        by_name = {e.method: e for e in entries}

        assert by_name["login"].http_method == "POST"
        assert by_name["login"].http_path == "/admin/login"

        assert by_name["getInfo"].http_method == "GET"
        assert by_name["getInfo"].http_path == "/admin/info"

    def test_bare_request_mapping_defaults_to_get(self, legacy_controller):
        entries = discover_api_entries(str(legacy_controller))
        by_name = {e.method: e for e in entries}

        # Bare @RequestMapping without method= defaults to GET
        assert by_name["logout"].http_method == "GET"
        assert by_name["logout"].http_path == "/admin/logout"


class TestNoClassPath:
    """Tests for controllers without class-level @RequestMapping."""

    def test_method_paths_used_directly(self, no_class_path_controller):
        entries = discover_api_entries(str(no_class_path_controller))
        assert len(entries) == 2

        paths = {e.http_path for e in entries}
        assert paths == {"/health", "/metrics"}


class TestPackageFilter:
    """Tests for package_filter parameter."""

    def test_filter_includes_matching(self, java_controller):
        entries = discover_api_entries(
            str(java_controller), package_filter="com.example.demo"
        )
        assert len(entries) == 4

    def test_filter_excludes_non_matching(self, java_controller):
        entries = discover_api_entries(
            str(java_controller), package_filter="com.other.pkg"
        )
        assert len(entries) == 0


class TestExcludePatterns:
    """Tests for exclude_patterns parameter."""

    def test_excludes_test_directories(self, tmp_path):
        # Create a controller in a test directory
        code = textwrap.dedent("""\
            package com.example.test.controller;
            import org.springframework.web.bind.annotation.*;
            @RestController
            public class TestController {
                @GetMapping("/test")
                public String test() { return "test"; }
            }
        """)
        filepath = tmp_path / "src" / "test" / "java" / "TestController.java"
        filepath.parent.mkdir(parents=True)
        filepath.write_text(code)

        entries = discover_api_entries(str(tmp_path))
        assert len(entries) == 0  # excluded by default "test" pattern

    def test_custom_exclude_patterns(self, java_controller):
        # Exclude everything with "controller" in path
        entries = discover_api_entries(
            str(java_controller), exclude_patterns=["controller"]
        )
        assert len(entries) == 0


class TestRealProjects:
    """Integration tests against real example projects (if available)."""

    @pytest.mark.skipif(
        not os.path.exists("examples/java-microservice"),
        reason="examples/java-microservice not available"
    )
    def test_java_microservice(self):
        entries = discover_api_entries(
            "examples/java-microservice",
            package_filter="com.example.microservice"
        )
        assert len(entries) >= 5
        methods = {e.method for e in entries}
        assert "getUser" in methods
        assert "createUser" in methods
        assert "health" in methods

    @pytest.mark.skipif(
        not os.path.exists("examples/Apollo"),
        reason="examples/Apollo not available"
    )
    def test_apollo(self):
        entries = discover_api_entries(
            "examples/Apollo",
            package_filter="com.ctrip.framework.apollo"
        )
        assert len(entries) >= 50  # Apollo has many endpoints
        # Check a known endpoint
        assert any(
            e.http_path == "/apps" and e.http_method == "GET"
            for e in entries
        )
