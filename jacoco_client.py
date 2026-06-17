"""JaCoCo-based per-request line coverage tracer.

Mechanism
---------
1. JVM runs with JaCoCo agent in TCP-server mode (no extra Java code needed).
2. Before each HTTP request  → ``jacococli dump --reset`` (clears counters).
3. Make the HTTP request.
4. After the response        → ``jacococli dump`` (fetch exec data to file).
5. ``jacococli report`` produces an XML coverage report.
6. Parse XML for per-line coverage → {class_name: [covered line numbers]}.

TCP binary protocol is handled entirely by jacococli.jar itself, so there
is no fragile hand-rolled socket code here.

Start JVM with::

    -javaagent:/path/to/jacocoagent.jar=\\
        output=tcpserver,port=6300,\\
        includes=com.example.microservice.**

JARs are downloaded automatically from Maven Central on first use via
``download_jacoco()``.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

_JACOCO_VERSION = "0.8.12"
_MAVEN_BASE = "https://repo1.maven.org/maven2/org/jacoco"


# ── public helpers ────────────────────────────────────────────────────────────

def download_jacoco(dest_dir: str = ".", version: str = _JACOCO_VERSION) -> tuple[str, str]:
    """Download jacocoagent.jar and jacococli.jar if not already present.

    Returns ``(agent_jar_path, cli_jar_path)``.
    """
    specs = {
        "jacocoagent.jar": (
            f"{_MAVEN_BASE}/org.jacoco.agent/{version}"
            f"/org.jacoco.agent-{version}-runtime.jar"
        ),
        "jacococli.jar": (
            f"{_MAVEN_BASE}/org.jacoco.cli/{version}"
            f"/org.jacoco.cli-{version}-nodeps.jar"
        ),
    }
    paths: dict[str, str] = {}
    for name, url in specs.items():
        dest = os.path.join(dest_dir, name)
        if not os.path.exists(dest):
            print(f"  Downloading {name} …", flush=True)
            urllib.request.urlretrieve(url, dest)
            print(f"  → {dest}")
        paths[name] = dest
    return paths["jacocoagent.jar"], paths["jacococli.jar"]


# ── jacococli subprocess wrapper ─────────────────────────────────────────────

def _cli_dump(
    jacococli_jar: str,
    host: str,
    port: int,
    dest_file: str,
    *,
    reset: bool = False,
) -> None:
    """Run ``jacococli dump`` to fetch exec data from the JaCoCo TCP server.

    When *reset* is True the agent resets its counters after sending the data
    (i.e. ``dump=true, reset=true`` in the wire protocol).
    """
    cmd = ["java", "-jar", jacococli_jar,
           "dump",
           "--address", host,
           "--port", str(port),
           "--destfile", dest_file]
    if reset:
        cmd.append("--reset")
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        raise OSError(f"jacococli dump failed (rc={proc.returncode}): {stderr[:400]}")


# ── high-level tracer ─────────────────────────────────────────────────────────

class JacocoTracer:
    """Per-request line coverage via JaCoCo TCP server + jacococli.

    Uses ``jacococli dump`` for the TCP wire protocol (no hand-rolled sockets)
    and ``jacococli report`` for XML coverage report generation.

    Typical usage::

        tracer = JacocoTracer(
            classfiles_dir="examples/java-microservice/target/classes",
            source_dir="examples/java-microservice/src/main/java",
            jacococli_jar="jacococli.jar",
        )
        tracer.reset()                    # clear counters before request
        # ... make HTTP request ...
        covered = tracer.dump()           # {"com.example...UserService": [30,31,35]}
    """

    def __init__(
        self,
        *,
        tcp_host: str = "localhost",
        tcp_port: int = 6300,
        exec_file: str = "/tmp/jacoco_request.exec",
        classfiles_dir: str,
        source_dir: str = "",
        jacococli_jar: str,
    ) -> None:
        self._host          = tcp_host
        self._port          = tcp_port
        self._exec_file     = exec_file
        self._classfiles    = classfiles_dir
        self._source_dir    = source_dir
        self._jacococli_jar = jacococli_jar

    def reset(self) -> None:
        """Dump (to a temp file) and reset JaCoCo probe counters.

        The exec data produced here is discarded; we only want the reset.
        """
        with tempfile.NamedTemporaryFile(suffix=".exec", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            _cli_dump(self._jacococli_jar, self._host, self._port,
                      tmp_path, reset=True)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def dump(self) -> dict[str, list[int]]:
        """Fetch coverage data → XML report → {class_name: [line_numbers]}."""
        _cli_dump(self._jacococli_jar, self._host, self._port,
                  self._exec_file, reset=False)

        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
            xml_path = tmp.name
        try:
            cmd = ["java", "-jar", self._jacococli_jar,
                   "report", self._exec_file,
                   "--classfiles", self._classfiles]
            if self._source_dir:
                cmd += ["--sourcefiles", self._source_dir]
            cmd += ["--xml", xml_path]
            proc = subprocess.run(cmd, capture_output=True, timeout=30)
            if proc.returncode != 0:
                return {}
            return _parse_xml(xml_path)
        finally:
            try:
                os.unlink(xml_path)
            except OSError:
                pass


def _parse_xml(xml_path: str) -> dict[str, list[int]]:
    """Parse JaCoCo XML report → {fully_qualified_class_name: [covered lines]}.

    A line is considered *covered* when ``ci`` (covered instructions) > 0.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    result: dict[str, list[int]] = {}
    for pkg in root.findall("package"):
        pkg_name = pkg.get("name", "").replace("/", ".")
        for sf in pkg.findall("sourcefile"):
            fname = sf.get("name", "")
            cls = f"{pkg_name}.{fname[:-5]}" if fname.endswith(".java") else f"{pkg_name}.{fname}"
            covered = [
                int(ln.get("nr", 0))
                for ln in sf.findall("line")
                if int(ln.get("ci", 0)) > 0 and int(ln.get("nr", 0)) > 0
            ]
            if covered:
                result[cls] = sorted(covered)
    return result
