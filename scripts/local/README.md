#!/usr/bin/env bash
# music-ai 项目的本地启动脚本集合,放这里方便复用。
# ---------------------------------------------------------------------------
# 推荐使用流程(在项目根目录执行):
#   1. ./scripts/local/check_env.sh         # 确认环境齐全
#   2. ./scripts/local/setup_local.sh       # 装系统包、配 venv、跑迁移(只跑一次)
#   3. ./scripts/local/start_local.sh       # 一次性拉起 3 个服务(API / worker / 前端)
#   4. ./scripts/local/stop_local.sh        # 停掉所有本地服务
# ---------------------------------------------------------------------------
