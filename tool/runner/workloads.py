"""Read-only Node/Docker probes; project build commands belong in workflows."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat


def node_ready(output, env, major=None):
    ok, version = output(["node", "--version"], env)
    match = re.fullmatch(r"v([0-9]+)\.[0-9]+\.[0-9]+", version)
    return bool(ok and match and (major is None or match[1] == major))


def java_version(text: str) -> str | None:
    """Extract a stable Java major from java -version stdout/stderr."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    match = re.search(r'\b(?:openjdk|java) version "([0-9]+)(?:\.[0-9._]+)?"', first)
    if not match:
        return None
    # Java 8 and earlier report 1.8; modern pinned toolchains use their real major.
    return match[1]


@dataclass(frozen=True)
class DockerProbe:
    status: str
    cli: str | None = None
    context: str | None = None
    host: str | None = None
    socket: str | None = None
    server_version: str | None = None
    providers: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def detected(self) -> str:
        values = []
        for label, value in (("CLI", self.cli), ("context", self.context),
                             ("endpoint", self.host), ("server", self.server_version)):
            if value:
                values.append(f"{label}: {value}")
        if self.providers:
            values.append("providers: " + ", ".join(self.providers))
        return "; ".join(values) or "Docker was not detected"


def docker_probe(output, env, *, home: Path, which=shutil.which, uid=None) -> DockerProbe:
    """Diagnose a CI-owned local Docker endpoint without contacting remote daemons."""
    path = env.get("PATH")
    cli = which("docker", path=path)
    providers = tuple(name for name in ("colima", "orbctl") if which(name, path=path))
    if not cli:
        return DockerProbe("cli-missing", providers=providers)

    ok, context = output(["docker", "context", "show"], env)
    context = context.strip() if ok else ""
    if not context:
        return DockerProbe("context-missing", cli=cli, providers=providers)

    # The wrapper supplies the CI account's own Docker config and strips inherited
    # endpoint overrides. Inspect first: never probe a production SSH/TCP daemon.
    ok, endpoint_json = output(["docker", "context", "inspect", context,
                                "--format", "{{json .Endpoints.docker}}"], env)
    if not ok:
        return DockerProbe("context-invalid", cli=cli, context=context, providers=providers)
    try:
        endpoint = json.loads(endpoint_json)
        host = endpoint.get("Host") if isinstance(endpoint, dict) else None
    except (ValueError, TypeError):
        return DockerProbe("context-invalid", cli=cli, context=context, providers=providers)
    if not isinstance(host, str):
        return DockerProbe("context-invalid", cli=cli, context=context, providers=providers)
    if not host.startswith("unix:///"):
        return DockerProbe("remote-context", cli=cli, context=context, host=host,
                           providers=providers)
    if any(c.isspace() for c in host):
        return DockerProbe("endpoint-unsafe", cli=cli, context=context, host=host,
                           providers=providers)
    socket_value = host[len("unix://"):]
    socket_path = Path(socket_value)
    if ".." in PurePosixPath(socket_value).parts:
        return DockerProbe("endpoint-unsafe", cli=cli, context=context, host=host,
                           socket=socket_value, providers=providers)
    # A context pointing into another macOS user's home defeats account isolation.
    try:
        socket_path.relative_to(Path("/Users"))
        lexical_in_users = True
    except ValueError:
        lexical_in_users = False
    if lexical_in_users and not socket_path.is_relative_to(home):
        return DockerProbe("foreign-user-socket", cli=cli, context=context, host=host,
                           socket=socket_value, providers=providers)
    try:
        resolved_socket = socket_path.resolve(strict=True)
    except (FileNotFoundError, PermissionError, OSError):
        return DockerProbe("socket-missing", cli=cli, context=context, host=host,
                           socket=socket_value, providers=providers)
    try:
        resolved_socket.relative_to(Path("/Users"))
        resolved_in_users = True
    except ValueError:
        resolved_in_users = False
    if resolved_in_users and not resolved_socket.is_relative_to(home.resolve()):
        return DockerProbe("foreign-user-socket", cli=cli, context=context, host=host,
                           socket=socket_value, providers=providers)
    try:
        info = socket_path.stat()
    except (FileNotFoundError, PermissionError, OSError):  # Race after resolve.
        return DockerProbe("socket-missing", cli=cli, context=context, host=host,
                           socket=socket_value, providers=providers)
    if not stat.S_ISSOCK(info.st_mode):
        return DockerProbe("endpoint-not-socket", cli=cli, context=context, host=host,
                           socket=socket_value, providers=providers)
    uid = os.getuid() if uid is None else uid
    if resolved_in_users and info.st_uid != uid:
        return DockerProbe("foreign-user-socket", cli=cli, context=context, host=host,
                           socket=socket_value, providers=providers)
    if not os.access(socket_path, os.R_OK | os.W_OK):
        return DockerProbe("socket-permission", cli=cli, context=context, host=host,
                           socket=socket_value, providers=providers)
    ok, version = output(["docker", "--host", host, "info", "--format", "{{.ServerVersion}}"], env)
    if not ok or not version:
        return DockerProbe("daemon-unreachable", cli=cli, context=context, host=host,
                           socket=socket_value, providers=providers)
    return DockerProbe("ready", cli=cli, context=context, host=host, socket=socket_value,
                       server_version=version, providers=providers)


def docker_ready(output, env, *, home: Path | None = None, which=shutil.which, uid=None):
    """Compatibility boolean for callers that do not need structured diagnostics."""
    return docker_probe(output, env, home=home or Path.home(), which=which, uid=uid).ready


def tool_ready(output, env, tool, version):
    """Opt-in fixed --version probe; no manifest-supplied args or regexes."""
    ok, result = output([tool, "--version"], env)
    if not ok or not result:
        return False
    # One unambiguous stable numeric version on the first nonempty line.
    matches = re.findall(r"(?<![\w.])v?([0-9]+(?:\.[0-9]+){1,3})(?![\w.+-])", result.splitlines()[0])
    return len(matches) == 1 and (matches[0] == version or matches[0].startswith(version + "."))


def path_ready(path, home):
    """CI-local paths cannot escape through a symlink to another user's directory."""
    import os
    import stat
    if not path.is_dir():
        return False
    resolved = path.resolve()
    roots = (home.resolve(), Path("/opt/homebrew"), Path("/usr/local"))
    if not any(resolved.is_relative_to(root) for root in roots):
        return False
    # Homebrew symlinks into Cellar are normal; world-writable search paths are not.
    for parent in (resolved, *resolved.parents):
        if parent.stat().st_mode & stat.S_IWOTH:
            return False
        if parent == home.resolve() and parent.stat().st_uid != os.getuid():
            return False
        if parent in roots:
            break
    return True
