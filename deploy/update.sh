#!/usr/bin/env bash
# Deploy latest code: sudo bash /opt/beammods/app/deploy/update.sh
set -euo pipefail
BASE=/opt/beammods; APP=$BASE/app
sudo -u beammods git -C $APP pull --ff-only
sudo -u beammods $BASE/venv/bin/pip install -q -r $APP/bot/requirements.txt
systemctl restart beammods
sleep 3
systemctl --no-pager --lines=15 status beammods
curl -fsS http://127.0.0.1:8080/health && echo
