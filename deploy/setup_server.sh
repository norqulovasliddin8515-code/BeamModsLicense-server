#!/usr/bin/env bash
# One-time VPS bootstrap for BeamModsStudio (Ubuntu 22.04 / 24.04). Run as root:
#   sudo DOMAIN=api.example.com EMAIL=you@mail.com REPO=git@github.com:USER/REPO.git bash setup_server.sh
set -euo pipefail

: "${DOMAIN:?set DOMAIN=api.yourdomain.tld}"
: "${EMAIL:?set EMAIL for LetsEncrypt notices}"
: "${REPO:?set REPO=git@github.com:USER/REPO.git}"

APP_USER=beammods
BASE=/opt/beammods
APP=$BASE/app

echo "==> Packages"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
DEBIAN_FRONTEND=noninteractive apt-get -y install \
  luajit python3 python3-venv python3-pip git nginx certbot sqlite3 ufw fail2ban unattended-upgrades
python3 --version

echo "==> Firewall (SSH, HTTP, HTTPS only)"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "==> Service user"
id -u $APP_USER >/dev/null 2>&1 || useradd --system --create-home --home-dir $BASE --shell /bin/bash $APP_USER
mkdir -p $BASE/backups $BASE/keys $BASE/mod_sources /var/www/certbot
chmod 700 $BASE/keys
chown -R $APP_USER:$APP_USER $BASE

echo "==> Deploy key for git (read + write: the bot pushes mods.json)"
if [ ! -f $BASE/.ssh/id_ed25519 ]; then
  sudo -u $APP_USER mkdir -p $BASE/.ssh
  sudo -u $APP_USER ssh-keygen -t ed25519 -N "" -C "beammods-vps" -f $BASE/.ssh/id_ed25519
  sudo -u $APP_USER bash -c "ssh-keyscan github.com >> $BASE/.ssh/known_hosts"
fi
echo
echo "------------------------------------------------------------------"
echo " Add this key in GitHub -> repo -> Settings -> Deploy keys"
echo " and tick 'Allow write access':"
echo
cat $BASE/.ssh/id_ed25519.pub
echo "------------------------------------------------------------------"
read -rp "Press Enter after adding the deploy key... "

echo "==> Code"
if [ ! -d $APP/.git ]; then
  sudo -u $APP_USER git clone "$REPO" $APP
fi
sudo -u $APP_USER git -C $APP config user.name  "BeamModsStudio VPS"
sudo -u $APP_USER git -C $APP config user.email "bot@$DOMAIN"

echo "==> Python venv"
sudo -u $APP_USER python3 -m venv $BASE/venv
sudo -u $APP_USER $BASE/venv/bin/pip install --upgrade pip
sudo -u $APP_USER $BASE/venv/bin/pip install -r $APP/bot/requirements.txt

# Drop IPv6 listen lines if this VPS has no IPv6 (nginx would refuse to start)
v6filter() { if [ -s /proc/net/if_inet6 ]; then cat; else grep -v '\[::\]'; fi; }

echo "==> TLS certificate (bootstrap HTTP config first)"
rm -f /etc/nginx/sites-enabled/default
sed "s/api.example.com/$DOMAIN/g" $APP/deploy/nginx/beammods-bootstrap.conf | v6filter > /etc/nginx/sites-available/beammods.conf
ln -sf /etc/nginx/sites-available/beammods.conf /etc/nginx/sites-enabled/beammods.conf
nginx -t && systemctl reload nginx
certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" \
  --email "$EMAIL" --agree-tos --no-eff-email --non-interactive
# Reload nginx after every renewal (certbot.timer handles renewal itself)
install -d /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

echo "==> Full Nginx config"
mkdir -p /var/cache/nginx/beammods_photos
sed "s/api.example.com/$DOMAIN/g" $APP/deploy/nginx/beammods.conf | v6filter > /etc/nginx/sites-available/beammods.conf
nginx -t && systemctl reload nginx

echo "==> systemd unit"
cp $APP/deploy/systemd/beammods.service /etc/systemd/system/beammods.service
systemctl daemon-reload
systemctl enable beammods

echo "==> Nightly SQLite backup (03:30, keeps 14 days)"
cat > /etc/cron.d/beammods-backup <<CRON
30 3 * * * $APP_USER sqlite3 $APP/beamods.db ".backup '$BASE/backups/beamods-\$(date +\%F).db'" && find $BASE/backups -name 'beamods-*.db' -mtime +14 -delete
CRON

echo
echo "DONE. Next:"
echo "  1) copy .env  -> $APP/.env      (chmod 600, chown $APP_USER)"
echo "  2) copy beamods.db -> $APP/beamods.db"
echo "  3) systemctl start beammods && journalctl -u beammods -f"
