"""Versioned, declarative workload requirements. No executable configuration."""
from __future__ import annotations

import copy
import re

from i18n import tr

COMMON = {"repository", "label", "ci_user", "minimum_free_gib", "runner_version", "runner_sha256"}
LEGACY_FIELDS = COMMON | {"platforms", "xcode_version", "ruby_version"}
FIELDS = COMMON | {"schema_version", "capabilities", "versions", "required_tools"}
OPTIONAL_FIELDS = {"tool_versions", "path_prepend"}
CAPABILITIES = ("android", "docker", "ios", "node", "ruby")
PROFILES = {
    "generic": (),
    "node": ("node",),
    "backend": ("docker", "node"),
    "android": ("android", "ruby"),
    "ios": ("ios", "ruby"),
    "mobile": ("android", "ios", "ruby"),
}


def validate(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError(tr("config_fields"))
    legacy = "schema_version" not in value
    required = LEGACY_FIELDS if legacy else FIELDS
    allowed = required if legacy else FIELDS | OPTIONAL_FIELDS
    if not required <= set(value) <= allowed:
        raise ValueError(tr("config_fields"))
    patterns = {"repository": r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+",
                "label": r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}",
                "ci_user": r"[a-z][a-z0-9_]{0,30}",
                "runner_version": r"[0-9]+\.[0-9]+\.[0-9]+"}
    for name, pattern in patterns.items():
        if not isinstance(value[name], str) or not re.fullmatch(pattern, value[name]):
            raise ValueError(tr("config_field", name))
    if value["repository"].split("/")[1] in (".", ".."):
        raise ValueError(tr("repository_invalid"))
    if value["ci_user"] in ("root", "admin", "daemon", "nobody", "guest"):
        raise ValueError(tr("ci_required"))
    if type(value["minimum_free_gib"]) is not int or not 20 <= value["minimum_free_gib"] <= 1000:
        raise ValueError(tr("disk_invalid"))
    hashes = value["runner_sha256"]
    if not isinstance(hashes, dict) or set(hashes) != {"arm64", "x64"} or any(
            not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values()):
        raise ValueError(tr("hashes_required"))
    if legacy:
        if value["platforms"] not in (["android"], ["ios"], ["android", "ios"]):
            raise ValueError(tr("platforms_invalid"))
        for name in ("xcode_version", "ruby_version"):
            if not isinstance(value[name], str) or not re.fullmatch(r"[0-9]+\.[0-9]+", value[name]):
                raise ValueError(tr("config_field", name))
        return value
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise ValueError(tr("schema_unsupported"))
    caps = value["capabilities"]
    if (not isinstance(caps, list) or any(not isinstance(c, str) or c not in CAPABILITIES for c in caps)
            or len(caps) != len(set(caps))):
        raise ValueError(tr("capabilities_invalid"))
    versions = value["versions"]
    if not isinstance(versions, dict) or not set(versions) <= {"node", "ruby", "xcode"}:
        raise ValueError(tr("versions_invalid"))
    for name, version in versions.items():
        capability = "ios" if name == "xcode" else name
        pattern = r"[1-9][0-9]{0,2}" if name == "node" else r"[0-9]+\.[0-9]+"
        if capability not in caps or not isinstance(version, str) or not re.fullmatch(pattern, version):
            raise ValueError(tr("versions_invalid"))
    if ("ios" in caps and "xcode" not in versions) or ("ruby" in caps and "ruby" not in versions):
        raise ValueError(tr("versions_invalid"))
    tools = value["required_tools"]
    if (not isinstance(tools, list) or len(tools) > 32
            or any(not isinstance(t, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", t)
                   or t in (".", "..") for t in tools)
            or len(tools) != len(set(tools))):
        raise ValueError(tr("required_tools_invalid"))
    validate_extensions(value)
    return value


def normalized(value: dict) -> dict:
    """Upgrade in memory only. Never rewrite a user's existing config."""
    validate(value)
    if "schema_version" in value:
        return copy.deepcopy(value)
    result = {k: copy.deepcopy(value[k]) for k in COMMON}
    versions = {"ruby": value["ruby_version"]}
    if "ios" in value["platforms"]:
        versions["xcode"] = value["xcode_version"]
    return dict(result, schema_version=2, capabilities=sorted([*value["platforms"], "ruby"]),
                versions=versions, required_tools=[])


def for_profile(base: dict, profile: str, *, extra=(), tools=(), node=None, ruby="3.3", xcode="26.3") -> dict:
    if profile not in PROFILES:
        raise ValueError(tr("profile_invalid"))
    caps = sorted(set(PROFILES[profile]) | set(extra))
    versions = {}
    if "ruby" in caps:
        versions["ruby"] = ruby
    if "ios" in caps:
        versions["xcode"] = xcode
    if node is not None:
        versions["node"] = node
    return with_requirements(base, dict(capabilities=caps, versions=versions, required_tools=list(tools)))


# No arbitrary environment variables, executable arguments, regexes or hooks.
REQUIREMENT_FIELDS = {"capabilities", "versions", "required_tools", "tool_versions",
                      "path_prepend", "minimum_free_gib"}


def validate_extensions(value: dict):
    constraints = value.get("tool_versions", {})
    if (not isinstance(constraints, dict) or not set(constraints) <= set(value["required_tools"])
            or any(not isinstance(v, str) or not re.fullmatch(r"[0-9]{1,4}(?:\.[0-9]{1,4}){0,3}", v)
                   for v in constraints.values())):
        raise ValueError(tr("tool_versions_invalid"))
    # A built-in probe owns its version contract; don't silently disagree with it.
    reserved = set()
    for capability in value["capabilities"]:
        reserved.update({"node": ("node", "npm"), "ruby": ("ruby",), "ios": ("xcodebuild", "xcrun"),
                         "android": ("sdkmanager", "avdmanager", "adb", "emulator"),
                         "docker": ("docker",)}[capability])
    if reserved & set(constraints):
        raise ValueError(tr("tool_versions_invalid"))
    paths = value.get("path_prepend", [])
    if not isinstance(paths, list) or len(paths) > 16:
        raise ValueError(tr("paths_invalid"))
    for path in paths:
        if not isinstance(path, str) or len(path) > 256:
            raise ValueError(tr("paths_invalid"))
        prefixes = ("${HOME}/", "/opt/homebrew/opt/", "/usr/local/opt/")
        prefix = next((p for p in prefixes if path.startswith(p)), None)
        if prefix is None:
            raise ValueError(tr("paths_invalid"))
        parts = path[len(prefix):].split("/")
        if any(p in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9_.@+-]+", p) for p in parts):
            raise ValueError(tr("paths_invalid"))
    if len(paths) != len(set(paths)):
        raise ValueError(tr("paths_invalid"))


def with_requirements(base: dict, requirements: dict) -> dict:
    """Apply one portable manifest. Identity/checksums remain local, no deep merge magic."""
    if not isinstance(requirements, dict) or not set(requirements) <= REQUIREMENT_FIELDS:
        raise ValueError(tr("requirements_invalid"))
    value = {k: copy.deepcopy(base[k]) for k in COMMON}
    value.update(schema_version=2, capabilities=[], versions={}, required_tools=[])
    value.update(copy.deepcopy(requirements))
    return validate(value)
