#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-rbxnotify.ru}"
PROJECT_DIR="${2:-/root/tgbot}"
SERVICE_NAME="${3:-rbxnotifybot}"
APP_PORT="${WEBAPP_PORT:-8080}"
APP_HOST="127.0.0.1"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this script as root."
  exit 1
fi

cd "$PROJECT_DIR"

echo "Installing system packages..."
apt update
apt install -y nginx python3-venv python3-pip certbot python3-certbot-nginx

echo "Preparing Python virtualenv..."
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "Updating .env WebApp settings..."
touch .env
grep -q '^WEBAPP_URL=' .env && sed -i "s#^WEBAPP_URL=.*#WEBAPP_URL=https://${DOMAIN}#" .env || echo "WEBAPP_URL=https://${DOMAIN}" >> .env
grep -q '^WEBAPP_HOST=' .env && sed -i "s#^WEBAPP_HOST=.*#WEBAPP_HOST=${APP_HOST}#" .env || echo "WEBAPP_HOST=${APP_HOST}" >> .env
grep -q '^WEBAPP_PORT=' .env && sed -i "s#^WEBAPP_PORT=.*#WEBAPP_PORT=${APP_PORT}#" .env || echo "WEBAPP_PORT=${APP_PORT}" >> .env

echo "Creating systemd service..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SERVICE
[Unit]
Description=Roblox Notification Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/.venv/bin/python ${PROJECT_DIR}/bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "Creating Nginx config..."
cat > "/etc/nginx/sites-available/${DOMAIN}" <<NGINX
server {
    listen 80;
    server_name ${DOMAIN} www.${DOMAIN};

    location / {
        proxy_pass http://${APP_HOST}:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX

ln -sf "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

echo "Requesting Let's Encrypt certificate..."
certbot --nginx -d "${DOMAIN}" -d "www.${DOMAIN}" --non-interactive --agree-tos --register-unsafely-without-email || {
  echo "Certbot failed. HTTP proxy is configured; try opening http://${DOMAIN}, then rerun:"
  echo "certbot --nginx -d ${DOMAIN} -d www.${DOMAIN}"
  exit 1
}

systemctl restart nginx
systemctl restart "${SERVICE_NAME}"

echo "Done."
echo "Check: https://${DOMAIN}"
echo "Logs: journalctl -u ${SERVICE_NAME} -f"
