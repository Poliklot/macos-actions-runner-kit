#!/usr/bin/env python3
"""Small foreground wrapper around the official runner; standard library only.

No GitHub login/PAT management, background service, automatic dispatch or fallback.
Privileged provisioning is separate from registration and job execution.
"""
from __future__ import annotations

import fcntl
import grp
import getpass
import warnings
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import shlex
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid

from keychain_state import default_keychain
from ios_platform_probe import available as ios_platform_available
import i18n
import configuration
import workloads
from i18n import tr

ROOT = Path(__file__).resolve().parent


def config(path: Path) -> dict:
    if not path.exists():
        raise ValueError(tr('config_missing'))
    return validate_config(json.loads(path.read_text(encoding="utf-8-sig")))


def validate_config(value: dict) -> dict:
    return configuration.validate(value)


def configure(args):
    """Create non-secret local settings without root, network or overwriting files."""
    if os.getuid() == 0:
        raise ValueError(tr('setup_no_root'))
    if args.config.exists() or args.config.is_symlink():
        raise ValueError(tr('config_exists'))
    if args.repository.lower() in ('owner/repo', 'your-org/your-repo', 'example/mobile-app'):
        raise ValueError(tr('config_placeholder'))
    value = config(ROOT / 'config.example.json')
    value.update(repository=args.repository, label=args.label, ci_user=args.ci_user)
    custom = args.profile is not None or args.capability or args.require_tool or args.node_version is not None
    if args.requirements is not None:
        if custom or args.platforms is not None or args.ruby_version is not None or args.xcode_version is not None:
            raise ValueError(tr('requirements_conflict'))
        value = configuration.with_requirements(value, json.loads(args.requirements.read_text(encoding="utf-8-sig")))
    elif custom:
        if args.platforms is not None:
            raise ValueError(tr('profile_platform_conflict'))
        value = configuration.for_profile(value, args.profile or 'generic', extra=args.capability,
                                          tools=args.require_tool, node=args.node_version,
                                          ruby=args.ruby_version or "3.3", xcode=args.xcode_version or "26.3")
        if ((args.ruby_version is not None and "ruby" not in value["capabilities"])
                or (args.xcode_version is not None and "ios" not in value["capabilities"])):
            raise ValueError(tr("versions_invalid"))
    else:
        # Preserve the original CLI contract and existing mobile installations.
        value.update(platforms=['android', 'ios'] if args.platforms in (None, 'all') else [args.platforms],
                     xcode_version=args.xcode_version or "26.3", ruby_version=args.ruby_version or "3.3")
    validate_config(value)
    # O_EXCL refuses existing files and symlinks; no overwrite/force option.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(args.config, flags, 0o600)
    except FileExistsError:
        raise ValueError(tr('config_exists')) from None
    with os.fdopen(fd, 'w', encoding='utf-8') as output_file:
        output_file.write(json.dumps(value, indent=2) + '\n')
    print(tr('configured', args.config, shlex.quote(str(args.config.resolve()))))
    return 0


def run(args, *, env=None, cwd=None, capture=True, timeout=None):
    return subprocess.run(args, env=env, cwd=cwd, text=True, capture_output=capture,
                          check=False, timeout=timeout)


def output(args, env=None):
    try:
        result = run(args, env=env, timeout=15)
        return result.returncode == 0, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


# Only these fixed variables may be exported by shell-env. Never print inherited secrets.
MANAGED_VARS = ("PATH", "ANDROID_HOME", "ANDROID_SDK_ROOT", "DEVELOPER_DIR", "DOCKER_CONFIG")
DOCKER_OVERRIDES = ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")


def environment(cfg: dict, home: Path, sdk: Path | None = None) -> dict:
    cfg = configuration.normalized(cfg)
    caps, versions = cfg["capabilities"], cfg["versions"]
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("ACTIONS_RUNNER_INPUT_")}
    for key in (*MANAGED_VARS, *DOCKER_OVERRIDES):
        env.pop(key, None)
    env["HOME"] = str(home)
    search = [home / "bin"]
    search.extend(Path(p.replace("${HOME}", str(home), 1)) for p in cfg.get("path_prepend", []))
    if "ruby" in caps:
        version = versions["ruby"]
        search.extend([Path(f"/opt/homebrew/opt/ruby@{version}/bin"),
                       Path(f"/usr/local/opt/ruby@{version}/bin")])
    if "node" in caps and "node" in versions:
        version = versions["node"]
        search.extend([Path(f"/opt/homebrew/opt/node@{version}/bin"),
                       Path(f"/usr/local/opt/node@{version}/bin")])
    search.extend([Path("/opt/homebrew/bin"), Path("/opt/homebrew/sbin")])
    if "android" in caps:
        sdk = sdk or home / "Library/Android/sdk"
        env.update(ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk))
        search.extend([sdk / "platform-tools", sdk / "emulator", sdk / "cmdline-tools/latest/bin"])
    env["PATH"] = ":".join(map(str, search)) + ":/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    if "ios" in caps:
        xcode = Path(f'/Applications/Xcode_{versions["xcode"]}.app/Contents/Developer')
        env["DEVELOPER_DIR"] = str(xcode if xcode.is_dir() else Path("/Applications/Xcode.app/Contents/Developer"))
    if "docker" in caps:
        env["DOCKER_CONFIG"] = str(home / ".docker")
    return env


def shell_environment(cfg: dict, home: Path) -> str:
    env = environment(cfg, home)
    lines = ["# Managed CI-only environment; no credentials."]
    lines.extend("unset " + key for key in DOCKER_OVERRIDES)
    lines.extend("export " + key + "=" + shlex.quote(env[key]) if key in env else "unset " + key
                 for key in MANAGED_VARS)
    return "\n".join(lines) + "\n"


def current_ci(cfg: dict) -> bool:
    user = pwd.getpwuid(os.getuid())
    return (user.pw_name == cfg["ci_user"] and os.getuid() != 0
            and "admin" not in [grp.getgrgid(g).gr_name for g in os.getgroups()]
            and Path.home() == Path(user.pw_dir) == Path("/Users") / cfg["ci_user"])


def require_ci(cfg: dict):
    if not current_ci(cfg):
        raise ValueError(tr('ci_login', cfg["ci_user"], cfg["ci_user"]))


def doctor(cfg: dict, *, host=False, sdk=None, print_report=True) -> bool:
    cfg = configuration.normalized(cfg)
    caps, versions = cfg["capabilities"], cfg["versions"]
    home = Path.home()
    # Project-specific PATH and version probes belong to CI, not the owner's login.
    if host:
        cfg = dict(cfg, path_prepend=[])
    env = environment(cfg, home, sdk)
    for path in cfg.get("path_prepend", []):
        if not workloads.path_ready(Path(path.replace("${HOME}", str(home), 1)), home):
            raise ValueError(tr('tool_path_fix'))
    rows = []

    def check(ok, title, fix):
        rows.append((bool(ok), title, fix))

    check(platform.system() == "Darwin", "macOS", tr('mac_required'))
    check(platform.machine() in ("arm64", "x86_64"), tr('architecture'), tr('architecture_required'))
    free = shutil.disk_usage(home).free / 1024**3
    check(free >= cfg["minimum_free_gib"], tr('free_space', f"{free:.1f}"), tr('free_space_fix', cfg["minimum_free_gib"]))
    if not host:
        check(current_ci(cfg), tr('ci_user', cfg["ci_user"]),
              tr('ci_user_fix', cfg["ci_user"]))
    for tool in dict.fromkeys(["python3", "git", "curl", "jq", "gh",
                               *(cfg["required_tools"] if not host else [])]):
        check(shutil.which(tool, path=env["PATH"]), tr('command', tool),
              tr('tools_install'))
    if not host:
        for tool, version in cfg.get("tool_versions", {}).items():
            # Never execute manifest-selected tools under the owner's account or root.
            check(current_ci(cfg) and workloads.tool_ready(output, env, tool, version),
                  tool + " " + version, tr('tool_version_fix'))
    if "ruby" in caps:
        ok, version = output(["ruby", "-e", "print RUBY_VERSION"], env)
        check(ok and version.startswith(versions["ruby"] + "."), f"Ruby: {version or tr('missing')}",
              tr('ruby_fix', versions["ruby"]))
    if "node" in caps:
        check(workloads.node_ready(output, env, versions.get("node")),
              "Node.js" + (" " + versions["node"] if "node" in versions else ""), tr('node_fix'))
        check(output(["npm", "--version"], env)[0], "npm", tr('node_fix'))
    if "docker" in caps:
        if host:
            # Setup must not depend on (or contact) the owner's personal daemon.
            check(shutil.which("docker", path=env["PATH"]), "Docker CLI", tr('docker_fix'))
        else:
            check(workloads.docker_ready(output, env), tr('docker_daemon'), tr('docker_fix'))
    if "ios" in caps:
        ok, version = output(["xcodebuild", "-version"], env)
        check(ok and version.splitlines()[0:1] == [f'Xcode {versions["xcode"]}'],
              f'Xcode {versions["xcode"]}',
              f'https://developer.apple.com/download/all/?q=Xcode%20{versions["xcode"]}')
        ok, _ = output(["xcodebuild", "-checkFirstLaunchStatus"], env)
        sdk_ok, sdk_version = output(["xcrun", "--sdk", "iphoneos", "--show-sdk-version"], env)
        check(ok and sdk_ok, tr('ios_sdk', sdk_version or tr('not_ready')),
              tr('xcode_fix', versions["xcode"]))
        # SDK metadata can exist without installed/enabled platform support.
        check(ok and sdk_ok and ios_platform_available(env), tr('ios_build_destination'),
              tr('ios_platform_fix', versions["xcode"]))
        if not host and current_ci(cfg):
            try:
                paths = default_keychain()
                ok, _ = output(["security", "list-keychains", "-d", "user"], env)
                ok = ok and (not paths or Path(paths[0]).is_file())
                detail = tr('keychain_present') if paths else tr('keychain_absent')
                check(ok, tr("keychain_state", detail), tr('keychain_error'))
            except OSError as error:
                check(False, "Keychain API", str(error))
    if "android" in caps:
        sdk_path = Path(env["ANDROID_HOME"])
        required = ["platform-tools/adb", "emulator/emulator", "cmdline-tools/latest/bin/sdkmanager",
                    "cmdline-tools/latest/bin/avdmanager"]
        sdk_ok = all((sdk_path / p).is_file() and os.access(sdk_path / p, os.X_OK)
                     for p in required) and (sdk_path / "build-tools").is_dir()
        check(sdk_ok, f"Android SDK: {sdk_path}",
              tr('sdk_required'))
        check(not (sdk_path / ".local-ci-sdk-copy-in-progress").exists(), tr('sdk_copied'),
              tr('sdk_interrupted'))
        if sdk_ok and not host:
            check(sdk_path.resolve().is_relative_to(home.resolve()) and sdk_path.stat().st_uid == os.getuid(),
                  tr('sdk_owner'), tr('setup_repeat'))
            ok, _ = output([str(sdk_path / "cmdline-tools/latest/bin/sdkmanager"), "--version"], env)
            check(ok, "SDK Manager / Java", tr('jdk_required'))
            ok, _ = output([str(sdk_path / "emulator/emulator"), "-accel-check"], env)
            check(ok, tr('emulator_acceleration'), tr('emulator_fix'))
    if not host and ("android" in caps or "ios" in caps):
        # Detect recovery journals left by this repository's existing signing helper.
        journals = list((home / "Library/Caches").glob("*-signing-state"))
        check(not journals, tr('signing_clean'),
              tr('signing_recover'))
    if print_report:
        for ok, title, fix in rows:
            status = "OK" if ok else tr("needs_attention")
            print(f"{status}  {title}")
            if not ok:
                print(f"       {fix}")
        if host and "docker" in caps:
            print(tr('docker_ci_only'))
        print("\n" + (tr('doctor_ok')
                        if all(r[0] for r in rows) else tr('doctor_failed')))
    return all(row[0] for row in rows)


def setup(cfg, config_path, source_sdk):
    if os.getuid() == 0:
        raise ValueError(tr('setup_no_root'))
    if current_ci(cfg):
        raise ValueError(tr('setup_no_ci'))
    if "admin" not in [grp.getgrgid(g).gr_name for g in os.getgroups()]:
        raise ValueError(tr('setup_admin'))
    cfg = configuration.normalized(cfg)
    if source_sdk is not None and "android" not in cfg["capabilities"]:
        raise ValueError(tr('sdk_not_selected'))
    sdk = (Path(source_sdk or os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
                or Path.home() / "Library/Android/sdk").expanduser().resolve()
           if "android" in cfg["capabilities"] else Path("/"))
    if not doctor(cfg, host=True, sdk=sdk):
        return 1
    print(tr('setup_start', cfg["ci_user"]), flush=True)
    # Freeze validated settings for this setup; never rewrite the legacy source.
    # sudo runs outside the administrator's private checkout. Only known source
    # files are installed; configuration contains no executable hooks.
    with tempfile.TemporaryDirectory(prefix="ci-runner-config-") as temporary:
        snapshot = Path(temporary) / "config.json"
        snapshot.write_text(json.dumps(cfg) + "\n")
        snapshot.chmod(0o600)
        result = run(["sudo", "/bin/bash", str(ROOT / "provision.sh"), str(ROOT),
                      str(snapshot.resolve()), str(sdk), i18n.LANGUAGE], cwd="/", capture=False)
    return result.returncode


def runner_directory(home: Path) -> Path:
    target = home / "actions-runner"
    if target.is_symlink() or (target.exists() and target.stat().st_uid != os.getuid()):
        raise ValueError(tr('runner_directory'))
    return target


def download_runner(cfg, target, arch):
    if arch not in ("arm64", "x64"):
        raise ValueError(tr('architecture_invalid'))
    if target.exists():
        raise ValueError(tr('runner_exists'))
    version = cfg["runner_version"]
    url = f"https://github.com/actions/runner/releases/download/v{version}/actions-runner-osx-{arch}-{version}.tar.gz"
    with tempfile.TemporaryDirectory(prefix=".runner-download-", dir=target.parent) as temporary:
        temporary = Path(temporary)
        archive = temporary / "runner.tar.gz"
        digest = hashlib.sha256()
        print(tr('download', version, arch), flush=True)
        with urllib.request.urlopen(url, timeout=120) as source, archive.open("wb") as destination:
            while chunk := source.read(1024**2):
                digest.update(chunk)
                destination.write(chunk)
        if digest.hexdigest() != cfg["runner_sha256"][arch]:
            raise ValueError(tr('checksum_failed'))
        unpacked = temporary / "unpacked"
        unpacked.mkdir(mode=0o700)
        with tarfile.open(archive) as tar:
            tar.extractall(unpacked, filter="data")
        for name in ("config.sh", "run.sh", "bin/Runner.Listener"):
            if not (unpacked / name).is_file():
                raise ValueError(tr('archive_missing', name))
        unpacked.chmod(0o700)
        unpacked.rename(target)
        (target / ".kit-download.json").write_text(json.dumps({"version": version, "arch": arch}))


def registration_matches(target, cfg):
    record = target / ".runner"
    if not record.is_file():
        return False
    # Runner.Listener (.NET) can write JSON with a UTF-8 BOM on macOS.
    data = json.loads(record.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(tr('registration_invalid'))
    expected = f'https://github.com/{cfg["repository"]}'.lower()
    if str(data.get("gitHubUrl", "")).rstrip("/").lower() != expected:
        raise ValueError(tr('registration_foreign'))
    return True


def read_registration_token():
    # Fail closed instead of getpass's fallback to an echoed stdin read.
    if not sys.stdin.isatty():
        raise ValueError(tr("token_terminal"))
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            token = getpass.getpass(tr("token_prompt"))
        except (getpass.GetPassWarning, EOFError):
            raise ValueError(tr("token_terminal")) from None
    # Reject pasted commands, braces, spaces, control characters and PATs.
    # Do not assume a fixed token length or prefix that GitHub may change.
    if (not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", token)
            or token.lower().startswith(("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_"))):
        raise ValueError(tr("token_invalid"))
    return token


def register(cfg):
    require_ci(cfg)
    if not doctor(cfg):
        return 1
    target = runner_directory(Path.home())
    if registration_matches(target, cfg):
        print(tr('already_registered'))
        return 0
    if not target.exists():
        download_runner(cfg, target, "arm64" if platform.machine() == "arm64" else "x64")
    elif not (target / ".kit-download.json").is_file():
        raise ValueError(tr('unknown_install'))
    print(tr('registration_url', cfg["repository"]))
    print(tr("token_help"), flush=True)
    print(tr("registration_defaults", cfg["label"]), flush=True)
    token = read_registration_token()
    host_name = re.sub(r"[^a-zA-Z0-9_-]", "-", socket.gethostname().split(".")[0])[:24]
    # Teammates can have identical default Mac hostnames; never replace theirs.
    name = f'{host_name}-{cfg["ci_user"][:24]}-{uuid.uuid4().hex[:8]}'
    env = environment(cfg, Path.home())
    # Official supported input, consumed and masked by Runner.Listener.
    # The token never appears in argv, shell history, config.json or our logs.
    env["ACTIONS_RUNNER_INPUT_TOKEN"] = token
    print(tr("registering"), flush=True)
    try:
        result = run(["/bin/bash", "./config.sh", "--unattended", "--url", f'https://github.com/{cfg["repository"]}',
                      "--name", name, "--labels", cfg["label"], "--work", "_work"],
                     env=env, cwd=target)
        # Preserve diagnostics without translating GitHub's actual error.
        diagnostic = ((result.stdout or "") + (result.stderr or "")).replace(token, "***")
    finally:
        env.pop("ACTIONS_RUNNER_INPUT_TOKEN", None)
        token = None
    if result.returncode == 0 and not registration_matches(target, cfg):
        raise ValueError(tr('registration_missing'))
    if result.returncode == 0:
        print(tr('registration_ok'))
    else:
        print(tr("registration_failed"), file=sys.stderr)
        if re.search(r"\b(401|403|404)\b", diagnostic):
            print(tr("registration_auth_hint"), file=sys.stderr)
        print(tr("original_output"), file=sys.stderr)
        print(diagnostic, file=sys.stderr)
    return result.returncode


def start(cfg):
    require_ci(cfg)
    if not doctor(cfg):
        return 1
    target = runner_directory(Path.home())
    if not registration_matches(target, cfg):
        raise ValueError(tr('register_first'))
    if output(["/usr/bin/pgrep", "-u", str(os.getuid()), "-f", "[R]unner.Listener"])[0]:
        raise ValueError(tr('listener_running'))
    # Keep one foreground session. No stale PID files, PID-reuse kills or services.
    with (Path.home() / ".ci-runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(tr('session_running')) from None
        print(tr('start_help'), flush=True)
        print(tr("original_output"), flush=True)
        # Same foreground process group: terminal Ctrl+C reaches the official
        # runner. Parent ignores KeyboardInterrupt until that runner exits.
        child = subprocess.Popen(["/usr/bin/caffeinate", "-i", "/bin/bash", "./run.sh"],
                                 cwd=target, env=environment(cfg, Path.home()))
        while True:
            try:
                return child.wait()
            except KeyboardInterrupt:
                print(tr('stopping'), flush=True)


def main(argv=None):
    try:
        argv = i18n.select(list(sys.argv[1:] if argv is None else argv))
    except (ValueError, OSError) as error:
        print(tr("error", error), file=sys.stderr)
        return 2
    parser = i18n.Parser(prog="ci-runner", description=tr('description'))
    parser.add_argument("--lang", metavar="ru|en", help=tr("language_help"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.json", help=tr("config_help"))
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("configure", help=tr('configure_help'))
    c.add_argument("--repository", required=True, help=tr('repository_help'))
    c.add_argument("--label", default="local-macos", help=tr('label_help'))
    c.add_argument("--ci-user", default="ci", help=tr('ci_user_help'))
    c.add_argument("--platforms", choices=['all', 'android', 'ios'], default=None, help=tr('platforms_help'))
    c.add_argument("--xcode-version", default=None, help=tr('xcode_version_help'))
    c.add_argument("--ruby-version", default=None, help=tr('ruby_version_help'))
    c.add_argument("--requirements", type=Path, help=tr("requirements_help"))
    c.add_argument("--profile", choices=tuple(configuration.PROFILES), help=tr('profile_help'))
    c.add_argument("--capability", choices=configuration.CAPABILITIES, action="append", default=[], help=tr('capability_help'))
    c.add_argument("--require-tool", action="append", default=[], help=tr('required_tool_help'))
    c.add_argument("--node-version", help=tr('node_version_help'))
    sub.add_parser("profiles", help=tr('profiles_help'))
    sub.add_parser("shell-env", help=tr('shell_env_help'))
    d = sub.add_parser("doctor", help=tr('doctor_help'))
    d.add_argument("--host", action="store_true", help=tr('host_help'))
    s = sub.add_parser("setup", help=tr('setup_help'))
    s.add_argument("--source-sdk", help=tr('sdk_help'))
    sub.add_parser("register", help=tr('register_help'))
    sub.add_parser("start", help=tr('start_command_help'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'profiles':
            for name, caps in configuration.PROFILES.items():
                print(name + ": " + (", ".join(caps) or tr('base_tools_only')))
            return 0
        if args.command == 'configure':
            return configure(args)
        cfg = config(args.config)
        if args.command == "shell-env":
            print(shell_environment(cfg, Path.home()), end="")
            return 0
        if args.command == "doctor":
            return 0 if doctor(cfg, host=args.host) else 1
        if args.command == "setup":
            return setup(cfg, args.config, args.source_sdk)
        return register(cfg) if args.command == "register" else start(cfg)
    except (ValueError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(tr('error', error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n" + tr("cancelled"), file=sys.stderr)
        return 130


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())
