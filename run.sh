#!/bin/sh
# ddns6fw 辅助脚本（在 Debian 宿主机上运行，使用普通的 python 容器）
#
#   ./run.sh         启动后台容器（host 网络模式，自动重启）
#   ./run.sh debug   前台运行、不自动重启，用于排查崩溃原因
#   ./run.sh stop    停止并删除容器
#   ./run.sh logs    查看实时日志
#   ./run.sh shell   进入容器命令行
#   ./run.sh once    在容器内立即同步一次
#   ./run.sh check   检查映射目录里的文件是否齐全、能否编译
#
# 说明：脚本所在目录会被映射到容器内 /ddns，修改脚本无需重建容器。

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE="python:3.12-slim"
NAME="ddns6fw"

case "$1" in
  stop)
    docker rm -f "$NAME"
    ;;
  logs)
    docker logs -f "$NAME"
    ;;
  shell)
    if docker ps -a --format '{{.Names}}' | grep -q "^$NAME$"; then
      docker exec -it "$NAME" sh
    else
      docker run --rm -it --network host -v "$APP_DIR:/ddns" -w /ddns "$IMAGE" sh
    fi
    ;;
  once)
    if docker ps --format '{{.Names}}' | grep -q "^$NAME$"; then
      docker exec -it "$NAME" python /ddns/main.py --once
    else
      docker run --rm -it --network host -v "$APP_DIR:/ddns" -w /ddns "$IMAGE" python /ddns/main.py --once
    fi
    ;;
  check)
    docker run --rm -v "$APP_DIR:/ddns" -w /ddns "$IMAGE" \
      sh -c "ls -l /ddns && python -m py_compile /ddns/*.py && echo '文件检查通过'"
    ;;
  debug)
    docker rm -f "$NAME" >/dev/null 2>&1
    docker run --rm -it --network host -v "$APP_DIR:/ddns" -w /ddns "$IMAGE" \
      python /ddns/main.py
    ;;
  *)
    docker rm -f "$NAME" >/dev/null 2>&1
    docker run -d --name "$NAME" --restart unless-stopped --network host \
      -v "$APP_DIR:/ddns" -w /ddns "$IMAGE" \
      python /ddns/main.py
    echo "已启动容器 $NAME，Web 界面： http://<本机IP>:6060"
    ;;
esac
