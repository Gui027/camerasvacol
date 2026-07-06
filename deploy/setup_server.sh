#!/usr/bin/env bash
# Roda DENTRO da VM (Ubuntu), depois de subir o codigo do projeto para
# /home/vacol/camerasvacol via scp/git.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip ffmpeg

cd /home/vacol/camerasvacol
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements-api.txt

sudo cp deploy/vacol-api.service /etc/systemd/system/vacol-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now vacol-api
sudo systemctl status vacol-api --no-pager
