"""
Container health check for AutoCast (used by Coolify's CMD health check).

Exit 0 = healthy: the bot finished starting and its watchdog confirmed
Telegram + database recently. Exit 1 = starting, stopping or stuck.

Coolify → your app → Healthcheck → Check type: CMD → Command:
    python healthcheck.py
"""
import json, os, sys, time

path = os.environ.get("HEALTH_FILE", "/tmp/autocast.health")
try:
    with open(path) as f:
        hb = json.load(f)
except Exception as e:
    print(f"unhealthy: no heartbeat ({e})")
    sys.exit(1)

age = time.time() - float(hb.get("ts", 0))
limit = float(hb.get("stale_after", 180))
if not hb.get("ready"):
    print(f"unhealthy: stage={hb.get('stage')}")
    sys.exit(1)
if age > limit:
    print(f"unhealthy: last successful check {int(age)}s ago (limit {int(limit)}s)")
    sys.exit(1)
print(f"healthy: last check {int(age)}s ago")
sys.exit(0)
