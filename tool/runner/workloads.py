"""Read-only Node/Docker probes; project build commands belong in workflows."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re


def node_ready(output, env, major=None):
    ok, version = output(["node", "--version"], env)
    match = re.fullmatch(r"v([0-9]+)\.[0-9]+\.[0-9]+", version)
    return bool(ok and match and (major is None or match[1] == major))


def docker_ready(output, env):
    # The wrapper supplies the CI account's own Docker config and strips inherited
    # endpoint overrides. Inspect first: never probe a production SSH/TCP daemon.
    ok, context = output(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker}}"], env)
    if not ok:
        return False
    try:
        endpoint = json.loads(context)
        host = endpoint.get("Host") if isinstance(endpoint, dict) else None
    except (ValueError, TypeError):
        return False
    if not isinstance(host, str) or not host.startswith("unix:///") or any(c.isspace() for c in host):
        return False
    path = host[len("unix://"):]
    if ".." in PurePosixPath(path).parts:
        return False
    ok, version = output(["docker", "--host", host, "info", "--format", "{{.ServerVersion}}"], env)
    return bool(ok and version)


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
