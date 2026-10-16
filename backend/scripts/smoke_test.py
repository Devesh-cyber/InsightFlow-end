"""
End-to-end smoke test against a RUNNING backend with a REAL Supabase login.

Exercises every endpoint: upload (CSV + Excel), explore, cleaning
(preview/apply/revert), export, recipe, and finally deletes what it created.

Usage (from backend/, with the server running and .env filled in):
    python scripts/smoke_test.py you@example.com 'password'
    python scripts/smoke_test.py you@example.com 'password' --api https://your-api.onrender.com
    python scripts/smoke_test.py --token "$TOKEN"

Exit code 0 = everything passed.
"""

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from get_token import read_env  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
SAMPLES = HERE / "sample_data"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f": {detail}" if detail and not ok else (f"  ({detail})" if detail else "")))
    return ok


def login(email: str, password: str) -> str:
    env = read_env(HERE / ".env")
    url, key = env.get("SUPABASE_URL", "").rstrip("/"), env.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY in backend/.env first.")
    response = httpx.post(
        f"{url}/auth/v1/token?grant_type=password",
        json={"email": email, "password": password},
        headers={"apikey": key},
        timeout=20,
    )
    if response.status_code != 200:
        sys.exit(f"Login failed ({response.status_code}): {response.text}")
    return response.json()["access_token"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("email", nargs="?")
    parser.add_argument("password", nargs="?")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--token", help="use this access token instead of logging in")
    args = parser.parse_args()
    if not args.token and not (args.email and args.password):
        parser.error("give EMAIL PASSWORD, or --token")

    token = args.token or login(args.email, args.password)
    api = httpx.Client(base_url=args.api.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=180)
    created: list[str] = []

    def timed(method: str, path: str, **kwargs) -> httpx.Response:
        started = time.perf_counter()
        response = api.request(method, path, **kwargs)
        response.elapsed_ms = (time.perf_counter() - started) * 1000  # type: ignore[attr-defined]
        return response

    def ms(r) -> str:
        return f"{r.status_code}, {r.elapsed_ms:.0f} ms"

    try:
        print("\n1. System")
        r = timed("GET", "/health")
        check("health", r.status_code == 200, ms(r))
        r = timed("GET", "/v1/me")
        if not check("/v1/me with real token", r.status_code == 200 and r.json().get("usage") is not None, ms(r) if r.status_code == 200 else r.text):
            print("     -> usage is null: DATABASE_URL / SUPABASE_SECRET_KEY missing in .env?")
            return 1

        print("\n2. Upload")
        r = timed("POST", "/v1/datasets", params={"filename": "messy_test.csv"},
                  content=(SAMPLES / "messy_test.csv").read_bytes(), headers={"Content-Type": "application/octet-stream"})
        if not check("upload CSV", r.status_code == 201, ms(r) if r.status_code == 201 else r.text):
            return 1
        ds = r.json()
        created.append(ds["id"])
        dtypes = {c["name"]: c["dtype"] for c in ds["columns"]}
        check("zip codes kept as text", dtypes.get("Zip Code") == "text")
        check("dates detected", dtypes.get("Signup Date") == "date")
        check("suggestions present", any(s["id"] == "convert_to_number:Age" for s in ds["suggestions"]))

        r = timed("POST", "/v1/datasets", params={"filename": "sample_book.xlsx"},
                  content=(SAMPLES / "sample_book.xlsx").read_bytes(), headers={"Content-Type": "application/octet-stream"})
        check("upload XLSX", r.status_code == 201, ms(r) if r.status_code == 201 else r.text)
        if r.status_code == 201:
            book = r.json()
            created.append(book["id"])
            r = timed("POST", f"/v1/datasets/{book['id']}/sheet", json={"sheet_name": "Sales", "expected_version": 1})
            check("switch sheet", r.status_code == 200 and r.json()["sheet_name"] == "Sales", ms(r) if r.status_code == 200 else r.text)

        r = timed("GET", "/v1/datasets")
        check("list datasets", r.status_code == 200 and any(d["id"] == ds["id"] for d in r.json()["datasets"]), ms(r))

        print("\n3. Explore")
        base = f"/v1/datasets/{ds['id']}"
        for path, params in [
            ("/overview", {}), ("/rows", {"limit": 5}), ("/health", {}), ("/columns", {}),
            ("/columns/detail", {"name": "Age"}), ("/relationships", {"a": "Customer ID", "b": "City"}),
            ("/correlations", {}), ("/charts/options", {"x": "City"}), ("/charts/data", {"type": "bar", "x": "City"}),
            ("/recommendations", {}),
        ]:
            r = timed("GET", base + path, params=params)
            check(f"GET {path}", r.status_code == 200, ms(r) if r.status_code == 200 else r.text[:200])

        print("\n4. Clean")
        op = {"type": "convert_type", "params": {"column": "Age", "to": "integer", "treat_as_missing": ["?"]}}
        r = timed("POST", base + "/preview", json=op)
        check("preview", r.status_code == 200 and r.json()["effect"]["changed_per_column"] == {"Age": 1}, ms(r))
        r = timed("POST", base + "/apply", json={**op, "expected_version": 1})
        check("apply -> version 2", r.status_code == 200 and r.json()["dataset"]["current_version"] == 2, ms(r) if r.status_code == 200 else r.text)
        r = timed("POST", base + "/apply", json={**op, "expected_version": 1})
        check("stale apply rejected (409)", r.status_code == 409)
        r = timed("GET", base + "/versions")
        check("versions", r.status_code == 200 and len(r.json()["versions"]) == 2, ms(r))
        r = timed("POST", base + "/revert", json={"to_version": 1, "expected_version": 2})
        check("revert -> version 3", r.status_code == 200 and r.json()["current_version"] == 3, ms(r) if r.status_code == 200 else r.text)

        print("\n5. Export")
        r = timed("GET", base + "/export", params={"format": "csv", "version": 2})
        check("export CSV (v2)", r.status_code == 200 and b"00123" in r.content, ms(r))
        r = timed("GET", base + "/export", params={"format": "xlsx"})
        check("export XLSX", r.status_code == 200 and r.content[:2] == b"PK", ms(r))
        r = timed("GET", base + "/recipe")
        check("pandas recipe", r.status_code == 200 and "import pandas" in r.text, ms(r))
    finally:
        print("\n6. Cleanup")
        for dataset_id in created:
            r = api.delete(f"/v1/datasets/{dataset_id}")
            check(f"delete {dataset_id[:8]}…", r.status_code == 204)

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f". Failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
