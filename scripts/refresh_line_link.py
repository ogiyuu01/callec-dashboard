"""Fetch LINE link rate from line-harness Worker and write line_link.json.

設計:
  - Worker endpoint: GET /api/admin/line-link-stats?days=28 (Bearer auth)
  - 集計内容: 直近 N 日の Shopify 注文顧客に対する LINE 連動率
  - 出力: d-*/data/line_link.json (build_pm_data.py が KPI/signal 化)

環境変数:
  - LINE_HARNESS_API_KEY      (必須 / GitHub Actions secret)
  - LINE_HARNESS_WORKER_URL   (任意 / 既定 https://line-crm-worker.line-crm-api.workers.dev)
  - LINE_LINK_DAYS            (任意 / 既定 28)

未設定時は noop (KPI は build_pm_data.py 側で「未計測 red」表示)。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
DEFAULT_WORKER_URL = "https://line-crm-worker.call-and-response.workers.dev"
DEFAULT_DAYS = 28
ROOT = Path(__file__).resolve().parent.parent


def fetch_stats(worker_url: str, api_key: str, days: int) -> dict:
    url = f"{worker_url.rstrip('/')}/api/admin/line-link-stats?days={days}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> int:
    api_key = os.environ.get("LINE_HARNESS_API_KEY")
    if not api_key:
        print("LINE_HARNESS_API_KEY unset — skip (KPI will show 未計測)", file=sys.stderr)
        return 0

    worker_url = os.environ.get("LINE_HARNESS_WORKER_URL") or DEFAULT_WORKER_URL
    days = int(os.environ.get("LINE_LINK_DAYS") or DEFAULT_DAYS)

    output_dirs = sorted(ROOT.glob("d-*"))
    if not output_dirs:
        print("FATAL: no d-*/ dashboard directory found", file=sys.stderr)
        return 2
    out_path = output_dirs[0] / "data" / "line_link.json"

    try:
        body = fetch_stats(worker_url, api_key, days)
    except urllib.error.HTTPError as e:
        print(f"Worker HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Worker fetch failed: {e}", file=sys.stderr)
        return 1

    if not body.get("success"):
        print(f"Worker returned non-success: {body}", file=sys.stderr)
        return 1

    now = datetime.now(JST)
    payload = {
        "_meta": {
            "source": "line-crm-worker /api/admin/line-link-stats",
            "fetched_at": now.strftime("%Y-%m-%d %H:%M JST"),
            "days": days,
            "shop_domain": body.get("shop_domain"),
        },
        "linked_customers_28d": body.get("linked_ordered_customers", 0),
        "total_customers_28d": body.get("ordered_customers", 0),
        "total_linked_customers_cumulative": body.get("total_linked_customers", 0),
        "link_rate": body.get("line_link_rate", 0.0),
        "since": body.get("since"),
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[refresh_line_link] wrote {out_path} "
        f"(linked {payload['linked_customers_28d']}/{payload['total_customers_28d']} "
        f"= {payload['link_rate']*100:.1f}%)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
