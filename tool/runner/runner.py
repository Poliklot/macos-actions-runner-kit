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
from i18n import tr

ROOT = Path(__file__).resolve().parent
FIELDS = {"repository", "label", "ci_user", "platforms", "xcode_version", "ruby_version",
          "minimum_free_gib", "runner_version", "runner_sha256"}


def config(path: Path) -> dict:
    if not path.exists():
        raise ValueError(tr('config_missing'))
    return validate_config(json.loads(path.read_text(encoding="utf-8-sig")))


def validate_config(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError(tr('config_fields'))
    patterns = {"repository": r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+",
                "label": r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}",
                "ci_user": r"[a-z][a-z0-9_]{0,30}",
                "xcode_version": r"[0-9]+\.[0-9]+", "ruby_version": r"[0-9]+\.[0-9]+",
                "runner_version": r"[0-9]+\.[0-9]+\.[0-9]+"}
    for name, pattern in patterns.items():
        if not isinstance(value[name], str) or not re.fullmatch(pattern, value[name]):
            raise ValueError(tr('config_field', name))
    if value["repository"].split("/")[1] in (".", ".."):
        raise ValueError(tr('repository_invalid'))
    if value["ci_user"] in ("root", "admin", "daemon", "nobody", "guest"):
        raise ValueError(tr('ci_required'))
    if value["platforms"] not in (["android"], ["ios"], ["android", "ios"]):
        raise ValueError(tr('platforms_invalid'))
    if type(value["minimum_free_gib"]) is not int or not 20 <= value["minimum_free_gib"] <= 1000:
        raise ValueError(tr('disk_invalid'))
    hashes = value["runner_sha256"]
    if not isinstance(hashes, dict) or set(hashes) != {"arm64", "x64"} or any(
            not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values()):
        raise ValueError(tr('hashes_required'))
    return value


def configure(args):
    """Create non-secret local settings without root, network or overwriting files."""
    if os.getuid() == 0:
        raise ValueError(tr('setup_no_root'))
    if args.config.exists() or args.config.is_symlink():
        raise ValueError(tr('config_exists'))
    if args.repository.lower() in ('owner/repo', 'your-org/your-repo', 'example/mobile-app'):
        raise ValueError(tr('config_placeholder'))
    value = config(ROOT / 'config.example.json')
    value.update(repository=args.repository, label=args.label, ci_user=args.ci_user,
                 platforms=['android', 'ios'] if args.platforms == 'all' else [args.platforms],
                 xcode_version=args.xcode_version, ruby_version=args.ruby_version)
    validate_config(value)
    # O_EXCL refuses existing files and symlinks; no overwrite/force option.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(args.config, flags, 0o600)
    except FileExistsError:
        raise ValueError(tr('config_exists')) from None
    with os.fdopen(fd, 'w', encoding='utf-8') as output_file:
        output_file.write(json.dumps(value, indent=2) + '\n')
    print(tr('configured', args.config))
    return 0


def run(args, *, env=None, cwd=None, capture=True):
    return subprocess.run(args, env=env, cwd=cwd, text=True, capture_output=capture,
                          check=False)


def output(args, env=None):
    try:
        result = run(args, env=env)
        return result.returncode == 0, result.stdout.strip()
    except OSError:
        return False, ""


def environment(cfg: dict, home: Path, sdk: Path | None = None) -> dict:
    # Never inherit overrides such as --replace, --pat or a stale token.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("ACTIONS_RUNNER_INPUT_")}
    version = cfg["ruby_version"]
    sdk = sdk or home / "Library/Android/sdk"
    env.update(HOME=str(home), ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk))
    search = [home / "bin", Path(f"/opt/homebrew/opt/ruby@{version}/bin"),
              Path(f"/usr/local/opt/ruby@{version}/bin"), Path("/opt/homebrew/bin"),
              Path("/opt/homebrew/sbin"), sdk / "platform-tools", sdk / "emulator",
              sdk / "cmdline-tools/latest/bin"]
    env["PATH"] = ":".join(map(str, search)) + ":/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    if "ios" in cfg["platforms"]:
        xcode = Path(f'/Applications/Xcode_{cfg["xcode_version"]}.app/Contents/Developer')
        env["DEVELOPER_DIR"] = str(xcode if xcode.is_dir() else Path("/Applications/Xcode.app/Contents/Developer"))
    return env


def current_ci(cfg: dict) -> bool:
    user = pwd.getpwuid(os.getuid())
    return (user.pw_name == cfg["ci_user"] and os.getuid() != 0
            and "admin" not in [grp.getgrgid(g).gr_name for g in os.getgroups()]
            and Path.home() == Path(user.pw_dir) == Path("/Users") / cfg["ci_user"])


def require_ci(cfg: dict):
    if not current_ci(cfg):
        raise ValueError(tr('ci_login', cfg["ci_user"], cfg["ci_user"]))


def doctor(cfg: dict, *, host=False, sdk=None, print_report=True) -> bool:
    home = Path.home()
    env = environment(cfg, home, sdk)
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
    for tool in ("python3", "ruby", "git", "curl", "jq", "gh"):
        check(shutil.which(tool, path=env["PATH"]), tr('command', tool),
              tr('tools_install'))
    ok, version = output(["ruby", "-e", "print RUBY_VERSION"], env)
    check(ok and version.startswith(cfg["ruby_version"] + "."), f"Ruby: {version or tr('missing')}",
          tr('ruby_fix', cfg["ruby_version"]))
    if "ios" in cfg["platforms"]:
        ok, version = output(["xcodebuild", "-version"], env)
        check(ok and version.splitlines()[0:1] == [f'Xcode {cfg["xcode_version"]}'],
              f'Xcode {cfg["xcode_version"]}',
              f'https://developer.apple.com/download/all/?q=Xcode%20{cfg["xcode_version"]}')
        ok, _ = output(["xcodebuild", "-checkFirstLaunchStatus"], env)
        sdk_ok, sdk_version = output(["xcrun", "--sdk", "iphoneos", "--show-sdk-version"], env)
        check(ok and sdk_ok, tr('ios_sdk', sdk_version or tr('not_ready')),
              tr('xcode_fix', cfg["xcode_version"]))
        # SDK metadata can exist without installed/enabled platform support.
        check(ok and sdk_ok and ios_platform_available(env), tr('ios_build_destination'),
              tr('ios_platform_fix', cfg["xcode_version"]))
        if not host and current_ci(cfg):
            try:
                paths = default_keychain()
                ok, _ = output(["security", "list-keychains", "-d", "user"], env)
                ok = ok and (not paths or Path(paths[0]).is_file())
                detail = tr('keychain_present') if paths else tr('keychain_absent')
                check(ok, tr("keychain_state", detail), tr('keychain_error'))
            except OSError as error:
                check(False, "Keychain API", str(error))
    if "android" in cfg["platforms"]:
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
    if not host:
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
    sdk = Path(source_sdk or os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
               or Path.home() / "Library/Android/sdk").expanduser().resolve()
    if not doctor(cfg, host=True, sdk=sdk):
        return 1
    print(tr('setup_start', cfg["ci_user"]), flush=True)
    # CI subprocesses must not inherit a checkout inside the administrator's
    # private home. All input paths are absolute; / is accessible to both users.
    result = run(["sudo", "/bin/bash", str(ROOT / "provision.sh"), str(ROOT),
                  str(config_path.resolve()), str(sdk), i18n.LANGUAGE], cwd="/", capture=False)
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
    c.add_argument("--platforms", choices=['all', 'android', 'ios'], default='all', help=tr('platforms_help'))
    c.add_argument("--xcode-version", default='26.3', help=tr('xcode_version_help'))
    c.add_argument("--ruby-version", default='3.3', help=tr('ruby_version_help'))
    d = sub.add_parser("doctor", help=tr('doctor_help'))
    d.add_argument("--host", action="store_true", help=tr('host_help'))
    s = sub.add_parser("setup", help=tr('setup_help'))
    s.add_argument("--source-sdk", help=tr('sdk_help'))
    sub.add_parser("register", help=tr('register_help'))
    sub.add_parser("start", help=tr('start_command_help'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'configure':
            return configure(args)
        cfg = config(args.config)
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
