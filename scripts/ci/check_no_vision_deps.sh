#!/usr/bin/env bash
# CI 守卫：全仓库无人脸/人体识别/视频流依赖（SPEC §6.6 / §10）。
# M6 只接收聚合数，代码库不得出现视觉识别依赖。
# 只扫描 git 跟踪的仓库文件（排除 node_modules/.venv/dist 等构建产物与本地工具目录）。
set -euo pipefail

cd "$(dirname "$0")/../.."

# 违禁包/关键字（依赖清单与源码）
PATTERN='opencv|opencv-python|cv2|face_recognition|dlib|mediapipe|insightface|deepface|mtcnn|retinaface|人脸识别|人体识别|人脸检测'

echo "[no-vision] 扫描违禁视觉依赖（git 跟踪文件）..."
# SPEC.md 的『不做清单』与本脚本、CI 定义中出现这些词属正常，排除之。
if git grep -nIE "${PATTERN}" -- \
     ':!SPEC.md' \
     ':!scripts/ci/check_no_vision_deps.sh' \
     ':!.github/workflows/ci.yml'; then
  echo "[no-vision] 失败：发现视觉识别相关依赖/关键字" >&2
  exit 1
fi
echo "[no-vision] 通过：无 opencv/人脸/人体识别依赖。"
