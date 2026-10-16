"""
Get a real Supabase access token for a test user, so you can call the API
before the frontend exists.

Usage (from the backend/ folder, with .env filled in):
    python scripts/get_token.py you@example.com 'your-password'

Then:
    curl -H "Authorization: Bearer <token>" http://localhost:8000/v1/me

Uses only the Python standard library (no extra installs).
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    email, password = sys.argv[1], sys.argv[2]
    env = read_env(Path(__file__).resolve().parent.parent / ".env")
    url = env.get("SUPABASE_URL", "").rstrip("/")
    key = env.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not url or not key:
        print("Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY in backend/.env first.")
        return 2

    request = urllib.request.Request(
        f"{url}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"apikey": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        print(f"Login failed ({exc.code}): {exc.read().decode(errors='replace')}")
        return 1

    print(payload["access_token"])
    print(f"\n(expires in {payload.get('expires_in')} seconds)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
