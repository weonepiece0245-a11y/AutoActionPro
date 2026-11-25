#!/usr/bin/env bash
# 在云服务器(Ubuntu)上安装“星期五”云端后备,并设成开机自动运行、出错自动重启。
# 用法:在 assistant 文件夹里运行  sudo bash server-install.sh
# 没有 .env 的话会问你 Claude API Key 和 Discord Token(输入时不显示)。
# 装好后会显示一串 FRIDAY- 开头的配对码,贴到电脑上星期五的齿轮设定里。
# 时区默认吉隆坡,想改的话:sudo FRIDAY_TZ=Asia/Singapore bash server-install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
TZ_NAME="${FRIDAY_TZ:-Asia/Kuala_Lumpur}"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 运行:sudo bash server-install.sh"
  exit 1
fi
if [ ! -f "$APP_DIR/assistant.py" ]; then
  echo "这个文件夹里没有 assistant.py,请在 assistant 文件夹里运行。"
  exit 1
fi
if [ ! -f "$APP_DIR/.env" ]; then
  echo "第一次安装,需要两样东西(输入时屏幕上不会显示,贴上后按回车):"
  read -rsp "Claude API Key(sk-ant-api 开头): " API_KEY; echo
  read -rsp "Discord 机器人 Token: " DISCORD_TOKEN; echo
  printf 'ANTHROPIC_API_KEY=%s\nFRIDAY_DISCORD_TOKEN=%s\n' "$API_KEY" "$DISCORD_TOKEN" > "$APP_DIR/.env"
fi

echo "== 1/5 安装 Python =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv openssl curl

echo "== 2/5 安装星期五需要的库(要几分钟)=="
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements-server.txt"

echo "== 3/5 生成和电脑连接用的证书和密码 =="
if [ ! -f "$APP_DIR/cloud_cert.pem" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=friday-cloud" \
    -keyout "$APP_DIR/cloud_key.pem" -out "$APP_DIR/cloud_cert.pem" 2>/dev/null
fi
if ! grep -q '^FRIDAY_CLOUD_SECRET=' "$APP_DIR/.env"; then
  echo "FRIDAY_CLOUD_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$APP_DIR/.env"
fi

echo "== 4/5 创建运行账户、设置时区 =="
id -u friday >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin friday
chown -R friday:friday "$APP_DIR"
chmod 600 "$APP_DIR/.env" "$APP_DIR/cloud_key.pem"
timedatectl set-timezone "$TZ_NAME" || true

echo "== 5/5 设置开机自动运行 =="
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
# 用 443 端口(HTTPS)和电脑连接;普通账户要这个权限才能用 443
AmbientCapabilities=CAP_NET_BIND_SERVICE

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
echo "安装完成!看到上面有 active (running) 就说明星期五已经在云端运行了。"
echo
echo "========== 配对码(整串复制,贴到电脑上星期五的 齿轮设定 → 云端星期五)=========="
sudo -u friday "$APP_DIR/venv/bin/python" "$APP_DIR/friday_cloud.py" code || true
echo "=================================================================================="
echo "配对码里有连接密码,只贴到你自己的星期五里,不要发给别人。"
echo "查看实时日志:journalctl -u friday -f     重启:systemctl restart friday     停止:systemctl stop friday"
