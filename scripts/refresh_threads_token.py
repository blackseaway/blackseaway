"""Refresh the long-lived Threads token (valid 60 days) and print the new one.

The workflow stores the printed token back into the THREADS_TOKEN repository secret.
"""
import sys
import urllib.parse

from common import env, http_json

token = env("THREADS_TOKEN")
if not token:
    sys.exit("THREADS_TOKEN is not set")
res = http_json("https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token="
                + urllib.parse.quote(token))
new = res.get("access_token")
if not new:
    sys.exit(f"refresh failed: {res}")
days = int(res.get("expires_in", 0)) // 86400
print(f"[threads] token refreshed, valid for {days} days", file=sys.stderr)
print(new)
