"""WealthSail API smoke test — exercises every required endpoint end-to-end.

Run from the repo root:  .venv/bin/python tests/smoke_api.py
Requires: local API on 127.0.0.1:8000, local Postgres seeded (migrations 001-013).
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8000/api/v1"
PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main() -> int:
    client = httpx.Client(timeout=30, base_url=BASE)

    # ---- login (张明) -----------------------------------------------------
    r = client.post("/auth/login", json={"email": "zhangming@example.com", "password": "dev"})
    check("login 张明 200", r.status_code == 200, r.text[:200])
    login = r.json()["data"]
    token = login["accessToken"]
    check("login envelope meta", "meta" in r.json() and r.json()["meta"]["schemaVersion"] == 1)
    headers = {"Authorization": f"Bearer {token}"}

    # ---- portfolio --------------------------------------------------------
    r = client.get("/portfolio/summary", headers=headers)
    check("portfolio/summary 200", r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        d = r.json()["data"]
        check("summary totalAsset=506000.00", d["totalAsset"] == "506000.00", d["totalAsset"])
        check("summary cash=80000.00", d["cashBalance"] == "80000.00", d["cashBalance"])
        check("summary quality FRESH", d["qualityStatus"] == "FRESH", d["qualityStatus"])
        check("summary allocation has A股+现金", {a["category"] for a in d["allocation"]} >= {"A股", "现金"})

    r = client.get("/portfolio/positions", headers=headers)
    check("portfolio/positions 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        pos = r.json()["data"]
        check("positions has 600519", any(p["symbol"] == "600519" for p in pos), str(pos)[:200])
        if pos:
            check("position money is str", isinstance(pos[0]["marketValue"], str))

    r = client.get("/portfolio/nav-trend?days=30", headers=headers)
    check("portfolio/nav-trend 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        points = r.json()["data"]["points"]
        check("nav-trend 30 points", len(points) == 30, str(len(points)))
        check("nav-trend last=506000", points[-1]["totalNav"] == "506000.00", str(points[-1]))

    # ---- reports ----------------------------------------------------------
    # Query the deterministic seed date explicitly so this smoke test remains
    # rerunnable after it generates reports for the current trade date.
    seed_trade_date = "2026-08-24"
    r = client.get(f"/reports/list?tradeDate={seed_trade_date}", headers=headers)
    check("reports/list seeded date 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        reps = r.json()["data"]
        check("reports/list 4 sessions", len(reps) == 4, str(len(reps)))
        sessions = {x["session"] for x in reps}
        check("reports sessions complete", sessions == {"PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET"}, str(sessions))

    r = client.get(
        f"/reports/list?tradeDate={seed_trade_date}&session=PRE_MARKET",
        headers=headers,
    )
    check("reports/list filter PRE_MARKET", r.status_code == 200 and len(r.json()["data"]) == 1)

    r = client.get("/reports/72000000-0000-0000-0000-000000000002", headers=headers)
    check("reports/{id} detail 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        detail = r.json()["data"]
        check("detail version=2", detail["version"] == 2, str(detail["version"]))
        check("detail supersedes set", detail["supersedesReportId"] is not None)
        check("detail versionHistory has v1", any(h["version"] == 1 for h in detail["versionHistory"]))

    r = client.get("/reports/99999999-0000-0000-0000-000000000000", headers=headers)
    check("reports/{id} missing → NOT_FOUND", r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND")

    r = client.post("/reports/generate", json={"session": "INTRADAY"}, headers=headers)
    check("reports/generate 200", r.status_code == 200, r.text[:300])
    gen_first = r.json()["data"] if r.status_code == 200 else {}
    r = client.post("/reports/generate", json={"session": "INTRADAY"}, headers=headers)
    check("reports/generate replay 200", r.status_code == 200, r.text[:300])
    gen_replay = r.json()["data"] if r.status_code == 200 else {}
    check(
        "generate idempotent within 60s window",
        bool(gen_first) and gen_first["id"] == gen_replay["id"],
        f"{gen_first.get('id')} vs {gen_replay.get('id')}",
    )
    check("generate has items", bool(gen_first) and len(gen_first["items"]) >= 1)

    r = client.post("/reports/generate", json={"session": "FUND_1430"}, headers=headers)
    check("reports/generate FUND_1430 200", r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        gen = r.json()["data"]
        # A seed may belong to a different trade date.  In that case today's
        # report correctly starts at v1; version increments are scoped to the
        # tenant + session + trade-date chain, not global history.
        check("generate has valid positive version", gen["version"] >= 1, str(gen.get("version")))

    r = client.post("/reports/generate", json={"session": "BAD_SESSION"}, headers=headers)
    check("reports/generate invalid session → 400", r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR")

    # ---- risk -------------------------------------------------------------
    r = client.get("/risk/policies", headers=headers)
    check("risk/policies 200", r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        pol = r.json()["data"]
        check("policies current CONFIRMED v3", pol["current"]["version"] == 3 and pol["current"]["status"] == "CONFIRMED")
        check("policies budget singlePositionLimit=30.00", pol["budget"]["singlePositionLimit"] == "30.00")
        check("policies history >= 2", len(pol["history"]) >= 2)

    r = client.get("/risk/rules", headers=headers)
    check("risk/rules 200", r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        rules = r.json()["data"]
        check("rules list >= 3", len(rules["rules"]) >= 3)
        check("rules singlePositionLimit VIOLATION", any(c["check"] == "singlePositionLimit" and c["status"] == "VIOLATION" for c in rules["checks"]), str(rules["checks"]))

    # ---- identity ---------------------------------------------------------
    r = client.get("/auth/me", headers=headers)
    check("auth/me 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        me = r.json()["data"]
        check("me profile 张明", me["profile"]["nickname"] == "张明")
        check("me tenants >= 1", len(me["tenants"]) >= 1)

    r = client.post("/auth/wecom/binding", json={}, headers=headers)
    check("wecom/binding challenge 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        b = r.json()["data"]
        check("binding token WS- format", b["token"].startswith("WS-"), b["token"])
        check("binding expiresIn=900", b["expiresInSeconds"] == 900, str(b["expiresInSeconds"]))
        check("binding cannot be browser-consumed", "externalUserId" not in b)

    # ---- operations (OWNER only) ------------------------------------------
    for path in ("/operations/jobs", "/operations/freshness", "/operations/outbox", "/operations/audit?limit=10"):
        r = client.get(path, headers=headers)
        check(f"{path} 200", r.status_code == 200, r.text[:200])

    # ---- auth failures ----------------------------------------------------
    r = client.get("/portfolio/summary")
    check("no token → AUTH_REQUIRED", r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REQUIRED")
    r = client.get("/portfolio/summary", headers={"Authorization": "Bearer garbage.token.here"})
    check("bad token → AUTH_REQUIRED", r.status_code == 401)

    # ---- tenant isolation: 李华 sees only his own portfolio ----------------
    r2 = client.post("/auth/login", json={"email": "lihua@example.com", "password": "x"})
    token2 = r2.json()["data"]["accessToken"]
    h2 = {"Authorization": f"Bearer {token2}"}
    r2s = client.get("/portfolio/summary", headers=h2)
    check("李华 summary isolated (not 张明 data)", r2s.status_code in (200, 503), f"{r2s.status_code} {r2s.text[:200]}")
    if r2s.status_code == 200:
        d2 = r2s.json()["data"]
        check("李华 totalAsset=300000.00", d2["totalAsset"] == "300000.00", d2["totalAsset"])
        check("李华 0 positions", d2["positionCount"] == 0, str(d2["positionCount"]))
    else:
        # Tenant 2 has no NAV projections seeded → DATA_UNAVAILABLE is the
        # correct fail-closed isolation behavior; the point is it never sees
        # tenant 1's 506000.00.
        check("李华 DATA_UNAVAILABLE (no projections)", r2s.json()["error"]["code"] == "DATA_UNAVAILABLE", r2s.text[:200])
    r2r = client.get("/reports/list", headers=h2)
    check("李华 reports empty (isolated)", r2r.status_code == 200 and r2r.json()["data"] == [])

    # 李华 tries to read 张明's report via X-Tenant-Id spoof
    r3 = client.get("/reports/72000000-0000-0000-0000-000000000002", headers={**h2, "X-Tenant-Id": "00000000-0000-0000-0000-000000000001"})
    check("cross-tenant spoof blocked → 404/403", r3.status_code in (403, 404), str(r3.status_code))

    # 李华 is VIEWER-level (OWNER of his own tenant) — operations on his tenant OK
    r4 = client.get("/operations/jobs", headers=h2)
    check("李华 operations jobs (OWNER) 200", r4.status_code == 200, r4.text[:200])

    # ---- WeCom GET callback verification -----------------------------------
    token_w = "dev-wecom-callback-token"
    aes_key = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    from backend.integrations.wecom import wecom_encrypt

    ts = str(int(time.time()))
    nonce = "abc123"
    echostr_plain = "hello-echostr"
    echostr = wecom_encrypt(echostr_plain, aes_key, "ww-dev-corp-id")
    sig = hashlib.sha1("".join(sorted([token_w, ts, nonce, echostr])).encode()).hexdigest()
    r5 = client.get(f"/adapters/wecom/callback?msg_signature={sig}&timestamp={ts}&nonce={nonce}&echostr={echostr}")
    check("wecom GET verify returns echostr", r5.status_code == 200 and r5.text == echostr_plain, r5.text[:100])

    r5b = client.get(f"/adapters/wecom/callback?msg_signature=deadbeef&timestamp={ts}&nonce={nonce}&echostr={echostr}")
    check("wecom GET bad signature → 403", r5b.status_code == 403)

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
