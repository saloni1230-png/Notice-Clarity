#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -f .env ]; then cp .env.example .env; fi
docker compose up --build -d
printf 'App: http://localhost:65030\nVerification emails: http://localhost:8025\n'
