#!/usr/bin/env python3
"""Start a separate synthetic case. Existing resources are never adopted or reset."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys

ROOT = Path(__file__).absolute().parent.parent
STATE = ROOT / ".case-state"
SAFE_ENV = {"PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
            "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
            "ProgramFiles", "ProgramFiles(x86)", "LANG", "LC_ALL"}
CONTEXT: str | None = None
CONFIG_KEYS = {"CASE_PROJECT", "CASE_DATABASE", "CASE_DB_PASSWORD", "DATABASE_URL",
               "JWT_SECRET", "FIELD_ENCRYPTION_KEY", "INSTALLATION_ID", "CASE_REPORTS"}


def plain(path: Path) -> Path:
    path = path.absolute()
    for parent in [*reversed(path.parents), path]:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("refusing link or reparse point")
    return path


def write_new(path: Path, value: str) -> None:
    plain(path)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
    if os.name != "nt":
        path.chmod(0o600)


def load(mode: str) -> dict[str, str]:
    data = json.loads(plain(STATE / mode / "config.json").read_text(encoding="utf-8"))
    if set(data) != CONFIG_KEYS or any(not isinstance(value, str) for value in data.values()):
        raise RuntimeError("unexpected configuration keys or value types")
    if not re.fullmatch(f"jiuye-case-{mode}-" + r"[a-f0-9]{32}", data["INSTALLATION_ID"]):
        raise RuntimeError("unexpected installation identity")
    protected = [data[name] for name in ("CASE_DB_PASSWORD", "JWT_SECRET", "FIELD_ENCRYPTION_KEY")]
    if len(set(protected)) != 3 or any(not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", item) for item in protected):
        raise RuntimeError("invalid or reused generated credentials")
    if not re.fullmatch(f"jiuye_case_{mode}_" + r"[a-f0-9]{16}", data["CASE_PROJECT"]):
        raise RuntimeError("unexpected project identity")
    if data["CASE_DATABASE"] != data["CASE_PROJECT"]:
        raise RuntimeError("database identity mismatch")
    expected = f"postgresql+psycopg://case_user:{data['CASE_DB_PASSWORD']}@postgres:5432/{data['CASE_DATABASE']}"
    if data["DATABASE_URL"] != expected:
        raise RuntimeError("database target mismatch")
    if data["CASE_REPORTS"] != (STATE / mode / "reports").as_posix():
        raise RuntimeError("unexpected reports mount")
    return data


def invoke(args: list[str], config: dict[str, str], *, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV}
    env.update(config)
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    if args[0] == "docker" and CONTEXT is not None and args[1] != "context":
        args = ["docker", "--context", CONTEXT, *args[1:]]
    result = subprocess.run(args, cwd=plain(ROOT), env=env, text=True,
                            encoding="utf-8", errors="replace", capture_output=True, input=input_text)
    if check and result.returncode:
        # Keep pytest's stdout summary even when Docker progress fills stderr.
        for label, stream, limit in (
            ("command stdout", result.stdout, 16000),
            ("command stderr", result.stderr, 4000),
        ):
            output = stream
            for name in ("DATABASE_URL", "CASE_DB_PASSWORD", "JWT_SECRET", "FIELD_ENCRYPTION_KEY"):
                value = config.get(name, "")
                if value:
                    output = output.replace(value, "[REDACTED]")
            output = re.sub(
                r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                "[REDACTED_JWT]",
                output,
            )
            if output:
                print(f"[{label}]\n{output[-limit:]}", file=sys.stderr)
        raise RuntimeError(f"command exit {result.returncode}; no automatic retry")
    return result


def local_context(config: dict[str, str]) -> str:
    context = invoke(["docker", "context", "show"], config).stdout.strip()
    document = json.loads(invoke(["docker", "context", "inspect", context], config).stdout)
    endpoint = document[0]["Endpoints"]["docker"]["Host"]
    if not (endpoint in {"unix:///var/run/docker.sock", "npipe:////./pipe/docker_engine",
                         "npipe:////./pipe/dockerDesktopLinuxEngine"}
            or re.fullmatch(r"unix:///run/user/[0-9]+/docker.sock", endpoint)):
        raise RuntimeError("selected Docker context is not an accepted local engine")
    return context


def protect_directory(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700)
        return
    sid = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                          "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"],
                         text=True, capture_output=True, check=True).stdout.strip()
    if not re.fullmatch(r"S-1-[0-9-]+", sid):
        raise RuntimeError("could not determine current account ACL identity")
    subprocess.run(["icacls.exe", str(path), "/inheritance:r", "/grant:r",
                    f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                   capture_output=True, check=True)


def compose(mode: str, config: dict[str, str], *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return invoke(["docker", "compose", "--project-directory", str(ROOT),
                   "--env-file", str(STATE / mode / "empty.env"),
                   "--project-name", config["CASE_PROJECT"],
                   "--file", str(ROOT / "deploy" / f"case-{mode}.compose.yml"), *args], config, input_text=input_text)


def verify_topology(mode: str, config: dict[str, str]) -> None:
    document = json.loads(compose(mode, config, "config", "--format", "json").stdout)
    if document["name"] != config["CASE_PROJECT"]:
        raise RuntimeError("rendered project mismatch")
    services = document["services"]
    expected = {"postgres", "test"} if mode == "test" else {"postgres", "app", "frontend"}
    if set(services) != expected or set(document["volumes"]) != {"pgdata", "uploads"}:
        raise RuntimeError("unexpected services or storage")
    networks = document["networks"]
    if (set(networks) != {"case_internal"} or not networks["case_internal"].get("internal")
            or networks["case_internal"].get("external")
            or networks["case_internal"]["name"] != config["CASE_PROJECT"] + "_case_internal"):
        raise RuntimeError("runtime network must be internal")
    for volume in document["volumes"].values():
        if volume.get("external") or not volume["name"].startswith(config["CASE_PROJECT"] + "_"):
            raise RuntimeError("external or foreign volume")
    for name, service in services.items():
        forbidden = ("privileged", "network_mode", "restart", "container_name", "devices", "pid",
                     "ipc", "extra_hosts", "cap_add", "volumes_from", "external_links")
        if any(service.get(key) for key in forbidden):
            raise RuntimeError("unexpected host access or restart policy")
        if set(service["networks"]) != {"case_internal"}:
            raise RuntimeError("unexpected network")
        ports = service.get("ports", [])
        if mode == "demo" and name == "frontend" and len(ports) != 1:
            raise RuntimeError("missing demo loopback port")
        if ports and not (mode == "demo" and name == "frontend" and len(ports) == 1
                          and ports[0].get("host_ip") == "127.0.0.1"
                          and str(ports[0].get("published")) == "18090"
                          and ports[0].get("target") == 80):
            raise RuntimeError("unexpected exposed port")
        expected_mounts = ({("volume", "pgdata", "/var/lib/postgresql/data")} if name == "postgres"
                           else {("volume", "uploads", "/data/uploads")} if name in {"app", "test"}
                           else set())
        if name == "test":
            expected_mounts.add(("bind", config["CASE_REPORTS"], "/validation"))
        actual_mounts = {(mount["type"], Path(mount["source"]).as_posix() if mount["type"] == "bind"
                          else mount["source"], mount["target"]) for mount in service.get("volumes", [])}
        if actual_mounts != expected_mounts:
            raise RuntimeError("unexpected volume source or target")
        if name in {"app", "test", "frontend"}:
            build = service.get("build", {})
            context = ROOT / ("frontend" if name == "frontend" else "backend")
            if Path(build.get("context", "")) != plain(context):
                raise RuntimeError("unexpected build source")
            if name != "frontend" and build.get("target") != ("test" if name == "test" else "runtime"):
                raise RuntimeError("unexpected build stage")
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                if name != "test" or Path(mount["source"]) != Path(config["CASE_REPORTS"]):
                    raise RuntimeError("unexpected bind mount")
                plain(Path(mount["source"]))
            elif mount["type"] != "volume" or mount["source"] not in {"pgdata", "uploads"}:
                raise RuntimeError("unexpected storage mount")


def require_fresh(config: dict[str, str]) -> None:
    prefix = config["CASE_PROJECT"]
    for args in (["ps", "-a", "--format", "{{.Names}}"],
                 ["volume", "ls", "--format", "{{.Name}}"],
                 ["network", "ls", "--format", "{{.Name}}"]):
        names = invoke(["docker", *args], config).stdout.splitlines()
        if any(name == prefix or name.startswith(prefix + "_") or name.startswith(prefix + "-")
               for name in names):
            raise RuntimeError("project resources already exist; no adoption or reset")


def main() -> int:
    global CONTEXT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "check", "build", "test", "start", "seed", "status", "stop"])
    parser.add_argument("mode", choices=["test", "demo"])
    args = parser.parse_args()
    plain(ROOT)
    directory = plain(STATE / args.mode)
    if args.action == "init":
        if directory.exists():
            raise RuntimeError("configuration exists; refusing credential rotation")
        directory.mkdir(parents=True)
        protect_directory(directory)
        (directory / "reports").mkdir()
        project = f"jiuye_case_{args.mode}_{secrets.token_hex(8)}"
        password = secrets.token_urlsafe(40)
        data = {"CASE_PROJECT": project, "CASE_DATABASE": project,
                "CASE_DB_PASSWORD": password,
                "DATABASE_URL": f"postgresql+psycopg://case_user:{password}@postgres:5432/{project}",
                "JWT_SECRET": secrets.token_urlsafe(48), "FIELD_ENCRYPTION_KEY": secrets.token_urlsafe(48),
                "INSTALLATION_ID": f"jiuye-case-{args.mode}-{secrets.token_hex(16)}",
                "CASE_REPORTS": (directory / "reports").as_posix()}
        write_new(directory / "config.json", json.dumps(data, indent=2) + "\n")
        write_new(directory / "empty.env", "# Empty: use generated process-scoped configuration.\n")
        print(f"Created separate {args.mode} configuration; credential values are not displayed.")
        return 0
    config = load(args.mode)
    CONTEXT = local_context(config)
    verify_topology(args.mode, config)
    if args.action == "check":
        print("Rendered topology accepted; this does not test the database or application.")
    elif args.action == "build":
        compose(args.mode, config, "build", "--pull")
    elif args.action == "test" and args.mode == "test":
        require_fresh(config)
        compose(args.mode, config, "up", "--abort-on-container-exit", "--exit-code-from", "test")
        print("Test process passed. See the JUnit report in .case-state/test/reports.")
    elif args.action == "start" and args.mode == "demo":
        require_fresh(config)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 18090))
        compose(args.mode, config, "up", "-d", "--wait", "--wait-timeout", "180")
        print("Services started at http://127.0.0.1:18090 ; no data has been seeded.")
    elif args.action == "status":
        print(compose(args.mode, config, "ps", "--format", "json").stdout)
    elif args.action == "seed" and args.mode == "demo":
        accounts_path = plain(directory / "accounts.json")
        if accounts_path.exists():
            raise RuntimeError("seed mapping already exists; refusing a repeat or password rotation")
        passwords = {name: secrets.token_urlsafe(40) for name in ("admin", "office", "counselor", "leader")}
        payload = json.dumps(passwords)
        write_new(accounts_path, payload + "\n")
        result = compose(args.mode, config, "exec", "-T", "app", "python", "case_demo.py", input_text=payload)
        print(result.stdout)
        print("Generated login details are stored locally in .case-state/demo/accounts.json.")
    elif args.action == "stop":
        compose(args.mode, config, "stop")
        print("Stopped only this case project; dedicated data volumes remain.")
    else:
        raise RuntimeError("test requires test mode; start requires demo mode")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"[case] STOP: {message}", file=sys.stderr)
        raise SystemExit(1)
