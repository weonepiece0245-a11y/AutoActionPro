#!/usr/bin/env bash
# 在云服务器(Ubuntu)上安装“星期五”云端模式,并设成开机自动运行、出错自动重启。
# 用法:把整个 assistant 文件夹传到服务器后,在那个文件夹里运行  sudo bash server-install.sh
# 时区默认吉隆坡,想改的话:sudo FRIDAY_TZ=Asia/Singapore bash server-install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
TZ_NAME="${FRIDAY_TZ:-Asia/Kuala_Lumpur}"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 运行:sudo bash server-install.sh"
  exit 1
fi
if [ ! -f "$APP_DIR/assistant.py" ] || [ ! -f "$APP_DIR/.env" ]; then
  echo "这个文件夹里没有 assistant.py 或 .env,请确认整个 assistant 文件夹都传上来了。"
  exit 1
fi

echo "== 1/4 安装 Python =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv

echo "== 2/4 安装星期五需要的库(要几分钟)=="
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements-server.txt"

echo "== 3/4 创建运行账户、设置时区 =="
id -u friday >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin friday
chown -R friday:friday "$APP_DIR"
chmod 600 "$APP_DIR/.env"
timedatectl set-timezone "$TZ_NAME" || true

echo "== 4/4 设置开机自动运行 =="
cat > /etc/systemd/system/friday.service <<UNIT
[Unit]
Description=Friday assistant (cloud mode)
After=network-online.target
Wants=network-online.target

[Service]
User=friday
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/assistant.py --server
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
Environment=TZ=$TZ_NAME

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable friday
systemctl restart friday
sleep 8

echo
systemctl --no-pager --lines=15 status friday || true
echo
echo "安装完成!看到上面有 active (running) 和“Discord 已连接”就说明星期五已经在云端运行了。"
echo "查看实时日志:journalctl -u friday -f     重启:systemctl restart friday     停止:systemctl stop friday"
