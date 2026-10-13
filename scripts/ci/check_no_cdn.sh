#!/usr/bin/env bash
# CI 守卫：前端无任何 CDN 引用 / 外部脚本 / 遥测（SPEC §3 镜像层守卫）。
# 依赖必须经 npm 打包，禁止 <script src=http...> / <link href=http...> / 外部字体。
# 只扫描 git 跟踪的前端源文件。
set -euo pipefail

cd "$(dirname "$0")/../.."
FAIL=0

echo "[no-cdn] 扫描 frontend 源码的外链引用（git 跟踪文件）..."

# 1) http(s) 外链的 script/link
if git grep -nIE '(src|href)[[:space:]]*=[[:space:]]*["'"'"']https?://' -- \
     'frontend/index.html' 'frontend/src'; then
  echo "[no-cdn] 失败：发现外部 CDN/外链引用" >&2
  FAIL=1
fi

# 2) 常见遥测/公共 CDN 域名
if git grep -nIE 'googletagmanager|google-analytics|gtag\(|sentry\.io|cdn\.jsdelivr|unpkg\.com|cdnjs' -- \
     'frontend/index.html' 'frontend/src'; then
  echo "[no-cdn] 失败：发现遥测/公共 CDN 域名" >&2
  FAIL=1
fi

if [ "${FAIL}" -eq 0 ]; then
  echo "[no-cdn] 通过：无 CDN / 外链 / 遥测。"
fi
exit "${FAIL}"
