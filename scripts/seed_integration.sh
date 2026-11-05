#!/usr/bin/env bash
# Creates a project, client key and leaderboard on a running dev server; prints the API key.
set -euo pipefail
URL=${1:-http://localhost:8000}
A="Authorization: Bearer ${LIVEOPS_ADMIN_TOKEN:-dev-admin-token}"
J='Content-Type: application/json'
P=$(curl -sf -XPOST "$URL/admin/projects" -H "$A" -H "$J" -d "{\"slug\":\"it-$RANDOM$RANDOM\",\"name\":\"Integration\"}")
ENV=$(echo "$P" | python3 -c 'import json,sys;print([e["id"] for e in json.load(sys.stdin)["environments"] if e["name"]=="dev"][0])')
curl -sf -XPOST "$URL/admin/environments/$ENV/leaderboards" -H "$A" -H "$J" -d '{"key":"it-main","name":"IT"}' >/dev/null
curl -sf -XPOST "$URL/admin/environments/$ENV/api-keys" -H "$A" -H "$J" -d '{"kind":"client","name":"it"}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["key"])'
