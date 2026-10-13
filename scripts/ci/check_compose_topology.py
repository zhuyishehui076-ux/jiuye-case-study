#!/usr/bin/env python3
"""CI 守卫：双档位 compose 拓扑结构校验（SPEC §3 网络层守卫第③条）。

断言：
  demo 档 = {app, nginx, postgres}（无 ollama），且 app 至少挂一张非 internal 网络（egress）。
  local 档 = {app, nginx, postgres, ollama}，且 app 只挂 internal 网络（零出网锁定）。
  两档 nginx 均对宿主发布端口；app 均不发布端口。

用法：
  demo:  docker compose -f docker-compose.yml -f docker-compose.demo.yml --profile demo config --format json | this
  local: docker compose -f docker-compose.yml                          --profile local config --format json | this
或直接：python check_compose_topology.py   （自行调用 docker compose，需仓库根目录）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

DEMO_FILES = ["-f", "docker-compose.yml", "-f", "docker-compose.demo.yml"]
LOCAL_FILES = ["-f", "docker-compose.yml"]


def load(profile: str, files: list[str]) -> dict:
    env = {**os.environ, "APP_PROFILE": profile}
    proc = subprocess.run(
        ["docker", "compose", *files, "--profile", profile, "config", "--format", "json"],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"[compose-topology] docker compose config 失败（profile={profile}）")
    return json.loads(proc.stdout)


def check(profile: str, cfg: dict, *, expect: set[str], app_internal_only: bool) -> list[str]:
    errs: list[str] = []
    services = cfg.get("services", {})
    top_nets = cfg.get("networks", {})
    got = set(services)
    if got != expect:
        errs.append(f"[{profile}] 服务集不符：期望 {sorted(expect)}，实得 {sorted(got)}")

    # app 网络出网判定
    app_nets = list((services.get("app", {}).get("networks") or {}).keys())
    if not app_nets:
        errs.append(f"[{profile}] app 未挂任何网络")
    non_internal = [n for n in app_nets if not top_nets.get(n, {}).get("internal", False)]
    if app_internal_only:
        if non_internal:
            errs.append(f"[{profile}] app 挂了非 internal 网络（零出网违规）：{non_internal}")
    else:
        if not non_internal:
            errs.append(f"[{profile}] app 无任何非 internal 网络，无法 egress（期望 demo 可出网）")

    # 端口暴露面：仅 nginx 发布端口，app 不发布
    if services.get("app", {}).get("ports"):
        errs.append(f"[{profile}] app 不应对宿主发布端口")
    if not services.get("nginx", {}).get("ports"):
        errs.append(f"[{profile}] nginx 应对宿主发布端口")
    return errs


def main() -> int:
    errs: list[str] = []
    demo = load("demo", DEMO_FILES)
    local = load("local", LOCAL_FILES)
    errs += check("demo", demo, expect={"app", "nginx", "postgres"}, app_internal_only=False)
    errs += check("local", local, expect={"app", "nginx", "postgres", "ollama"}, app_internal_only=True)
    if errs:
        print("[compose-topology] 失败：", file=sys.stderr)
        for e in errs:
            print("  - " + e, file=sys.stderr)
        return 1
    print("[compose-topology] 通过：demo 可出网 / local 零出网 / 仅 nginx 暴露端口。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
