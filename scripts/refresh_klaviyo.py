"""Fetch Klaviyo flow metrics and write JSON for the Web dashboard.

設計:
  - Klaviyo flow-values-reports API から Live flow 別の売上/開封率/前期比を取得
  - 5 timeframe (7d / prev7d / 30d / prev30d / 365d) を取得
  - WoW% / MoM% / engagement / 閾値アラート計算
  - 出力: d-*/data/klaviyo.json

環境変数:
  - KLAVIYO_PRIVATE_API_KEY (GitHub Actions secret として設定)
  - KLAVIYO_API_REVISION (任意、既定 2024-10-15)

実行:
  python3 scripts/refresh_klaviyo.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import urllib.request
import urllib.error

BASE_URL = "https://a.klaviyo.com"
DEFAULT_REVISION = "2024-10-15"
MAX_RETRIES = 5
ROOT = Path(__file__).resolve().parent.parent

# Output target: dashboard token directory
OUTPUT_DIRS = sorted(ROOT.glob("d-*"))
if not OUTPUT_DIRS:
    print("FATAL: no d-*/ dashboard directory found", file=sys.stderr)
    sys.exit(2)
OUT = OUTPUT_DIRS[0] / "data" / "klaviyo.json"


def kapi(method: str, path: str, api_key: str, revision: str, body: Optional[dict] = None) -> Any:
    """Minimal Klaviyo API client (stdlib only, no requests dependency)."""
    url = path if path.startswith("http") else f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision": revision,
        "accept": "application/json",
        "content-type": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    backoff = 1.0
    for _ in range(MAX_RETRIES):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", backoff))
                time.sleep(retry_after)
                backoff = min(backoff * 2, 30)
                continue
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Klaviyo API {e.code}: {err_body[:500]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Klaviyo API URL error: {e}")
    raise RuntimeError("Klaviyo API: exceeded retry budget")


def iter_pages(path: str, api_key: str, revision: str, params: Optional[dict] = None):
    """Iterate cursor-paginated endpoints. Yields data items."""
    next_url: Optional[str] = None
    qs = ""
    if params:
        from urllib.parse import urlencode
        qs = "?" + urlencode(params)
    while True:
        payload = kapi("GET", next_url or (path + qs), api_key, revision)
        for item in payload.get("data", []):
            yield item
        next_url = (payload.get("links") or {}).get("next")
        if not next_url:
            return


def fetch_period(api_key: str, revision: str, conv_id: str, filt: str, timeframe: dict, flow_ids: list[str]) -> dict[str, dict]:
    """Fetch flow-values-reports for one timeframe, aggregate by flow_id."""
    body = {
        "data": {
            "type": "flow-values-report",
            "attributes": {
                "timeframe": timeframe,
                "statistics": [
                    "recipients", "opens_unique", "open_rate",
                    "clicks_unique", "click_rate",
                    "conversions", "conversion_rate", "conversion_value",
                ],
                "conversion_metric_id": conv_id,
                "filter": filt,
            },
        }
    }
    result = kapi("POST", "/api/flow-values-reports", api_key, revision, body)
    agg: dict[str, dict] = {fid: {"recipients": 0, "opens": 0, "clicks": 0, "convs": 0, "rev": 0.0} for fid in flow_ids}
    for row in result.get("data", {}).get("attributes", {}).get("results", []):
        fid = row.get("groupings", {}).get("flow_id")
        if not fid or fid not in agg:
            continue
        s = row.get("statistics", {})
        agg[fid]["recipients"] += s.get("recipients", 0) or 0
        agg[fid]["opens"] += s.get("opens_unique", 0) or 0
        agg[fid]["clicks"] += s.get("clicks_unique", 0) or 0
        agg[fid]["convs"] += s.get("conversions", 0) or 0
        agg[fid]["rev"] += s.get("conversion_value", 0) or 0
    for vals in agg.values():
        r = vals["recipients"] or 0
        vals["open_rate"] = (vals["opens"] / r) if r else 0.0
        vals["click_rate"] = (vals["clicks"] / r) if r else 0.0
        vals["conv_rate"] = (vals["convs"] / r) if r else 0.0
    return agg


def change_pct(curr: float, prev: float) -> Optional[float]:
    if prev == 0 or prev is None:
        return None
    return (curr - prev) / prev * 100


def alert_open(rate: float) -> str:
    if rate is None or rate == 0:
        return "neutral"
    if rate < 0.15:
        return "red"
    if rate < 0.25:
        return "yellow"
    return "green"


def alert_change(pct: Optional[float]) -> str:
    if pct is None:
        return "neutral"
    if pct < -20:
        return "red"
    if pct < -5:
        return "yellow"
    return "green"


def main() -> int:
    api_key = os.environ.get("KLAVIYO_PRIVATE_API_KEY", "").strip()
    if not api_key or api_key.startswith("pk_xxxx"):
        # Write empty placeholder so dashboard JS can show skip message
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "last_updated": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M JST"),
            "error": "KLAVIYO_PRIVATE_API_KEY not configured",
            "flows": [],
            "totals": {},
            "alerts": [],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[skip] no api key, wrote placeholder to {OUT}")
        return 0
    revision = os.environ.get("KLAVIYO_API_REVISION", DEFAULT_REVISION).strip()

    print("[step] fetching live flows ...")
    try:
        live_flows = []
        for f in iter_pages("/api/flows", api_key, revision):
            attrs = f.get("attributes", {})
            if attrs.get("status") == "live":
                live_flows.append({"id": f["id"], "name": attrs.get("name", "")})
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    if not live_flows:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "last_updated": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M JST"),
            "flows": [],
            "totals": {},
            "alerts": [],
            "info": "No live flows yet",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[done] no live flows, wrote {OUT}")
        return 0
    print(f"[step] found {len(live_flows)} live flow(s): {[f['name'] for f in live_flows]}")

    print("[step] fetching Placed Order metric_id ...")
    conv_id = None
    for m in iter_pages("/api/metrics", api_key, revision):
        if m.get("attributes", {}).get("name") == "Placed Order":
            conv_id = m["id"]
            break
    if not conv_id:
        print("FATAL: Placed Order metric not found", file=sys.stderr)
        return 1
    print(f"[step] conv_id = {conv_id}")

    flow_ids = [f["id"] for f in live_flows]
    filt = 'contains-any(flow_id,' + json.dumps(flow_ids).replace(' ', '') + ')'

    today = date.today()
    iso = lambda d: d.isoformat() + "T00:00:00+00:00"
    timeframes = {
        "current_7d": {"key": "last_7_days"},
        "previous_7d": {"start": iso(today - timedelta(days=14)), "end": iso(today - timedelta(days=7))},
        "current_30d": {"key": "last_30_days"},
        "previous_30d": {"start": iso(today - timedelta(days=60)), "end": iso(today - timedelta(days=30))},
        "annual": {"key": "last_365_days"},
    }

    data: dict[str, dict[str, dict]] = {}
    for label, tf in timeframes.items():
        print(f"[step] fetching {label} ...")
        data[label] = fetch_period(api_key, revision, conv_id, filt, tf, flow_ids)

    # Build JSON output
    flows_out = []
    total_c7 = total_p7 = total_c30 = total_p30 = total_365 = 0.0
    for f in live_flows:
        fid = f["id"]
        c7 = data["current_7d"][fid]
        p7 = data["previous_7d"][fid]
        c30 = data["current_30d"][fid]
        p30 = data["previous_30d"][fid]
        an = data["annual"][fid]
        total_c7 += c7["rev"]; total_p7 += p7["rev"]
        total_c30 += c30["rev"]; total_p30 += p30["rev"]
        total_365 += an["rev"]
        wow = change_pct(c7["rev"], p7["rev"])
        mom = change_pct(c30["rev"], p30["rev"])
        rpr = (c30["rev"] / c30["recipients"]) if c30["recipients"] else 0
        flows_out.append({
            "flow_id": fid,
            "name": f["name"],
            "current_7d": c7,
            "previous_7d": p7,
            "current_30d": c30,
            "previous_30d": p30,
            "annual": an,
            "wow_pct": wow,
            "mom_pct": mom,
            "open_alert": alert_open(c30["open_rate"]),
            "wow_alert": alert_change(wow),
            "mom_alert": alert_change(mom),
            "rpr_30d": rpr,
        })

    total_wow = change_pct(total_c7, total_p7)
    total_mom = change_pct(total_c30, total_p30)

    # Alerts
    alerts: list[dict] = []
    for fo in flows_out:
        c30 = fo["current_30d"]
        if c30["recipients"] >= 50:
            if c30["open_rate"] < 0.15:
                alerts.append({
                    "level": "red",
                    "flow": fo["name"],
                    "kind": "low_open_rate",
                    "value": c30["open_rate"],
                    "msg": f"開封率 {c30['open_rate']*100:.1f}% (業界下限 15% 未満) — 件名・preheader 見直し検討",
                })
        wow = fo["wow_pct"]
        if wow is not None and wow < -20:
            alerts.append({
                "level": "red",
                "flow": fo["name"],
                "kind": "wow_drop",
                "value": wow,
                "msg": f"売上 WoW {wow:+.1f}% (前週比 -20% 以下) — リスト健全性 / 件名変更 / 配信時刻を確認",
            })

    out_json = {
        "last_updated": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M JST"),
        "flows": flows_out,
        "totals": {
            "rev_7d": total_c7,
            "rev_7d_prev": total_p7,
            "rev_30d": total_c30,
            "rev_30d_prev": total_p30,
            "rev_365d": total_365,
            "wow_pct": total_wow,
            "mom_pct": total_mom,
            "wow_alert": alert_change(total_wow),
            "mom_alert": alert_change(total_mom),
        },
        "alerts": alerts,
        "legend": {
            "open_rate": {"green": ">=0.25", "yellow": "0.15-0.25", "red": "<0.15"},
            "change_pct": {"green": ">=-5", "yellow": "-20 to -5", "red": "<-20"},
            "alert_min_recipients": 50,
            "attribution_window_days": 5,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {OUT} ({len(flows_out)} flows, {len(alerts)} alerts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
