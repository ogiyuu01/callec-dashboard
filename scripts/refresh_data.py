"""BigQuery + ローカル CSV からダッシュボード用 JSON を生成する。

ローカル実行: python3 scripts/refresh_data.py
GH Actions: GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json python3 scripts/refresh_data.py

出力: d-<token>/data/{summary,funnel,channels,utm_health,releases}.json
"""
from __future__ import annotations

import json
import os
import sys
import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

from google.cloud import bigquery

ROOT = Path(__file__).resolve().parent.parent
TOKEN = (ROOT / ".token").read_text().strip()
DATA_DIR = ROOT / f"d-{TOKEN}" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

PROJECT = "gen-lang-client-0015689236"
DATASET = "analytics_320051621"

# 隣接の作業リポジトリから CSV を読む
SIBLING = ROOT.parent / "shopify-ec-automation"
RELEASE_LOG = SIBLING / "data" / "release_log.csv"
WEEKLY_KPI = SIBLING / "data" / "weekly_kpi.csv"


def write_json(name: str, payload: dict) -> None:
    p = DATA_DIR / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  wrote {p.relative_to(ROOT)}")


def yen(v): return "¥" + f"{int(round(v or 0)):,}"
def num(v): return f"{int(round(v or 0)):,}"
def pct(v, d=2): return "—" if v is None else f"{v*100:.{d}f}%"


def make_narrative(last7: dict, prev7: dict, yoy: dict) -> dict:
    """非分析者向けの平易な日本語サマリを生成する。"""
    lines = []
    actions = []

    sales = last7.get("sales") or 0
    p_sales = prev7.get("sales") or 0
    sales_wow = (sales - p_sales) / p_sales * 100 if p_sales else 0

    orders = last7.get("orders") or 0
    p_orders = prev7.get("orders") or 0

    cvr = last7.get("cvr") or 0
    y_cvr = yoy.get("cvr") or 0
    cvr_yoy = (cvr - y_cvr) / y_cvr * 100 if y_cvr else 0

    items = last7.get("items_per_session") or 0

    # 1. 売上サマリ
    arrow = "📈" if sales_wow >= 5 else "📉" if sales_wow <= -5 else "→"
    lines.append(f"{arrow} 今週の売上 {yen(sales)}（先週より {sales_wow:+.0f}%）")
    lines.append(f"   注文は {orders} 件 / 先週は {p_orders} 件")

    # 2. CVR 状態
    if cvr_yoy < -20:
        lines.append(f"⚠️ 買ってくれる人の割合（CVR）が前年より大きく低下しています（{cvr*100:.2f}% vs 前年 {y_cvr*100:.2f}%）")
    elif cvr_yoy > 5:
        lines.append(f"✅ 買ってくれる人の割合（CVR）は前年より改善しています（{cvr*100:.2f}%）")
    else:
        lines.append(f"📊 買ってくれる人の割合（CVR）は前年並みです（{cvr*100:.2f}%）")

    # 3. items/session
    if items < 1.2:
        lines.append(f"🔴 1セッションあたりの商品閲覧数 {items:.2f} は低めです。商品ページにたどり着けていない人が多い状態です。")
    elif items < 1.4:
        lines.append(f"🟡 1セッションあたりの商品閲覧数 {items:.2f} は平均的。R001 反映でこれを 1.40 以上に上げるのが目標です。")
    else:
        lines.append(f"🟢 1セッションあたりの商品閲覧数 {items:.2f} は良好です。")

    # アクション
    actions.append("R001（コレクションページ改善）を本番に反映する")
    actions.append("メルマガ配信時にURLに utm を手で書かない（Shopify Email 自動UTMに任せる）")
    actions.append("LINE 配信時は ?utm_source=line&utm_medium=line&utm_campaign=日付_内容 を必ず付ける")

    return {"lines": lines, "actions": actions}


def update_archive(summary: dict) -> None:
    """毎週月曜の "確定スナップショット" を archive.json に追記する。
    実装: 直近の完了週 (週末=日曜終わり) を ISO 週ラベルで保存。同一週は上書き。"""
    archive_path = DATA_DIR / "archive.json"
    existing = []
    if archive_path.exists():
        try:
            existing = json.loads(archive_path.read_text(encoding="utf-8")).get("weeks", [])
        except Exception:
            existing = []

    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    iso_y, iso_w, _ = last_monday.isocalendar()
    week_label = f"{iso_y}-W{iso_w:02d}"

    entry = {
        "week": week_label,
        "start_date": last_monday.isoformat(),
        "end_date": last_sunday.isoformat(),
        "kpis": summary.get("last_7d", {}),
        "yoy": summary.get("yoy", {}),
        "prev": summary.get("prev_7d", {}),
        "narrative": summary.get("narrative", {}),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    weeks = [w for w in existing if w.get("week") != week_label]
    weeks.append(entry)
    weeks.sort(key=lambda w: w.get("week", ""), reverse=True)
    weeks = weeks[:52]  # 最大52週分

    archive_path.write_text(json.dumps({"weeks": weeks}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  archive: {week_label} ({len(weeks)} weeks stored)")


def q(client: bigquery.Client, sql: str) -> list[dict]:
    return [dict(r) for r in client.query(sql).result()]


def build_summary(client: bigquery.Client) -> dict:
    today = date.today()
    last_end = today - timedelta(days=1)
    last_start = last_end - timedelta(days=6)
    prev_end = last_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)
    yoy_end = last_end.replace(year=last_end.year - 1)
    yoy_start = last_start.replace(year=last_start.year - 1)

    def kpis(start: date, end: date) -> dict:
        sql = f"""
        SELECT
          COUNT(*) AS sessions,
          SUM(item_views) AS item_views,
          SUM(purchases) AS orders,
          CAST(SUM(revenue) AS INT64) AS sales,
          SAFE_DIVIDE(SUM(purchases), COUNT(*)) AS cvr,
          SAFE_DIVIDE(SUM(revenue), NULLIF(SUM(purchases),0)) AS aov,
          SAFE_DIVIDE(SUM(item_views), COUNT(*)) AS items_per_session
        FROM `{PROJECT}.{DATASET}.sessions_clean`
        WHERE event_date BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
        """
        rows = q(client, sql)
        if not rows:
            return {}
        r = rows[0]
        return {
            "sessions": int(r.get("sessions") or 0),
            "item_views": int(r.get("item_views") or 0),
            "orders": int(r.get("orders") or 0),
            "sales": int(r.get("sales") or 0),
            "cvr": float(r.get("cvr") or 0),
            "aov": float(r.get("aov") or 0),
            "items_per_session": float(r.get("items_per_session") or 0),
        }

    last_7d = kpis(last_start, last_end)
    prev_7d = kpis(prev_start, prev_end)
    yoy = kpis(yoy_start, yoy_end) if WEEKLY_KPI else {}
    # YoY via weekly_kpi.csv if BQ doesn't reach back that far
    if not yoy.get("sessions") and WEEKLY_KPI.exists():
        with WEEKLY_KPI.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        target_year = yoy_start.isocalendar()[0]
        target_wk = yoy_start.isocalendar()[1]
        label = f"{target_year}-W{target_wk:02d}"
        for r in rows:
            if r.get("week") == label:
                yoy = {
                    "sessions": int(r.get("sessions", 0) or 0),
                    "orders": int(r.get("orders", 0) or 0),
                    "sales": int(r.get("sales", 0) or 0),
                    "cvr": float(r.get("cvr", 0) or 0),
                    "aov": float(r.get("aov", 0) or 0),
                    "items_per_session": float(r.get("items_per_session", 0) or 0) if r.get("items_per_session") else 0,
                }
                break

    weeks = []
    if WEEKLY_KPI.exists():
        with WEEKLY_KPI.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows[-12:]:
            weeks.append({
                "week": r["week"],
                "sessions": int(r.get("sessions", 0) or 0),
                "orders": int(r.get("orders", 0) or 0),
                "sales": int(r.get("sales", 0) or 0),
                "cvr": float(r.get("cvr", 0) or 0),
                "items_per_session": float(r.get("items_per_session", 0) or 0) if r.get("items_per_session") else None,
            })

    signals = []
    if last_7d.get("items_per_session") is not None:
        v = last_7d["items_per_session"]
        signals.append({
            "level": "red" if v < 1.2 else "yellow" if v < 1.4 else "green",
            "metric": "items/session",
            "value": f"{v:.2f}",
            "target": "≥ 1.40 (R001目標)",
            "note": "R001 反映後の改善を測る最重要指標",
        })
    if last_7d.get("cvr") is not None:
        v = last_7d["cvr"]
        signals.append({
            "level": "red" if v < 0.005 else "yellow" if v < 0.007 else "green",
            "metric": "CVR",
            "value": f"{v*100:.2f}%",
            "target": "≥ 0.70%",
            "note": "前年同期 0.91%。回復目標は 0.7% 以上",
        })

    narrative = make_narrative(last_7d, prev_7d, yoy)

    return {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M JST"),
        "last_7d": last_7d,
        "prev_7d": prev_7d,
        "yoy": yoy,
        "weeks": weeks,
        "signals": signals,
        "narrative": narrative,
    }


def build_funnel(client: bigquery.Client) -> dict:
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=6)

    sql = f"""
    SELECT
      COUNT(*) AS sessions,
      SUM(list_views) AS list_views,
      SUM(item_views) AS item_views,
      SUM(atcs) AS atcs,
      SUM(checkouts) AS checkouts,
      SUM(purchases) AS purchases
    FROM `{PROJECT}.{DATASET}.sessions_clean`
    WHERE event_date BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
    """
    f = q(client, sql)[0]
    steps = [
        {"name": "Sessions", "count": int(f["sessions"] or 0)},
        {"name": "view_item", "count": int(f["item_views"] or 0)},
        {"name": "add_to_cart", "count": int(f["atcs"] or 0)},
        {"name": "begin_checkout", "count": int(f["checkouts"] or 0)},
        {"name": "purchase", "count": int(f["purchases"] or 0)},
    ]

    weeks = []
    if WEEKLY_KPI.exists():
        with WEEKLY_KPI.open(encoding="utf-8-sig", newline="") as fh:
            for r in list(csv.DictReader(fh))[-12:]:
                weeks.append({
                    "week": r["week"],
                    "items_per_session": float(r.get("items_per_session", 0) or 0) if r.get("items_per_session") else 0,
                    "cvr": float(r.get("cvr", 0) or 0),
                })

    sql_coll = f"""
    SELECT
      REGEXP_EXTRACT(landing_page, r'/collections/[^?#]+') AS path,
      COUNT(*) AS sessions,
      SUM(IF(item_views > 0, 1, 0)) AS pdp_reached
    FROM `{PROJECT}.{DATASET}.sessions_clean`
    WHERE event_date BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
      AND landing_page LIKE '%/collections/%'
    GROUP BY path
    HAVING sessions >= 30
    ORDER BY sessions DESC
    LIMIT 15
    """
    collections = []
    for r in q(client, sql_coll):
        path = unquote(r["path"]) if r["path"] else "—"
        sess = int(r["sessions"] or 0)
        pdp = int(r["pdp_reached"] or 0)
        collections.append({
            "path": path, "sessions": sess, "pdp_reached": pdp,
            "pdp_reach": 100 * pdp / sess if sess else 0,
        })

    return {"steps": steps, "weeks": weeks, "collections": collections}


def build_channels(client: bigquery.Client) -> dict:
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=6)
    start_trend = end - timedelta(days=12 * 7 - 1)

    case_channel = """
      CASE
        WHEN source = '(direct)' THEN 'Direct'
        WHEN medium = 'cpc' THEN 'Paid Search'
        WHEN medium = 'organic' THEN 'Organic Search'
        WHEN medium = 'email' THEN 'Email'
        WHEN medium = 'social' THEN 'Social'
        WHEN medium = 'referral' THEN 'Referral'
        WHEN medium = 'line' THEN 'LINE'
        ELSE 'Other'
      END AS channel
    """

    sql_now = f"""
    SELECT
      {case_channel},
      COUNT(*) AS sessions,
      SUM(purchases) AS orders,
      CAST(SUM(revenue) AS INT64) AS sales,
      SAFE_DIVIDE(SUM(purchases), COUNT(*)) AS cvr
    FROM `{PROJECT}.{DATASET}.sessions_clean`
    WHERE event_date BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
    GROUP BY channel
    ORDER BY sessions DESC
    """
    channels = []
    for r in q(client, sql_now):
        channels.append({
            "channel": r["channel"],
            "sessions": int(r["sessions"] or 0),
            "orders": int(r["orders"] or 0),
            "sales": int(r["sales"] or 0),
            "cvr": float(r["cvr"] or 0),
        })

    sql_trend = f"""
    SELECT
      FORMAT_DATE('%G-W%V', PARSE_DATE('%Y%m%d', event_date)) AS week,
      {case_channel},
      COUNT(*) AS sessions
    FROM `{PROJECT}.{DATASET}.sessions_clean`
    WHERE event_date BETWEEN '{start_trend.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
    GROUP BY week, channel
    """
    raw = q(client, sql_trend)
    weeks = sorted(set(r["week"] for r in raw))
    totals = {}
    for r in raw:
        totals[r["channel"]] = totals.get(r["channel"], 0) + (r["sessions"] or 0)
    chans = sorted(totals, key=lambda c: -totals[c])[:5]
    series = []
    for ch in chans:
        sessions = [next((r["sessions"] for r in raw if r["week"] == w and r["channel"] == ch), 0) for w in weeks]
        series.append({"channel": ch, "sessions": [int(x or 0) for x in sessions]})

    return {"channels": channels, "trend": {"weeks": weeks, "series": series}}


def build_utm_health(client: bigquery.Client) -> dict:
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=6)

    sql_broken = f"""
    SELECT traffic_source.source AS source, COUNT(*) AS sessions
    FROM `{PROJECT}.{DATASET}.events_*`
    WHERE _TABLE_SUFFIX BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
      AND event_name = 'session_start'
      AND (traffic_source.source LIKE '%&%' OR traffic_source.source LIKE '%=%' OR LENGTH(traffic_source.source) > 40)
    GROUP BY source
    ORDER BY sessions DESC
    """
    broken = [{"source": (r["source"] or "")[:60], "sessions": int(r["sessions"] or 0)} for r in q(client, sql_broken)]

    sql_se = f"""
    SELECT traffic_source.name AS campaign, COUNT(DISTINCT user_pseudo_id) AS users
    FROM `{PROJECT}.{DATASET}.events_*`
    WHERE _TABLE_SUFFIX BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
      AND event_name = 'session_start'
      AND traffic_source.source = 'shopify_email'
    GROUP BY campaign
    ORDER BY users DESC
    LIMIT 20
    """
    se = [{"campaign": r["campaign"], "users": int(r["users"] or 0)} for r in q(client, sql_se)]

    return {"broken": broken, "shopify_email_campaigns": se}


def build_releases() -> dict:
    releases = []
    if RELEASE_LOG.exists():
        with RELEASE_LOG.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                releases.append({
                    "release_id": r.get("release_id", ""),
                    "deployed_at": r.get("deployed_at", ""),
                    "type": r.get("type", ""),
                    "target": r.get("target", ""),
                    "summary": r.get("summary", ""),
                    "hypothesis_metric": r.get("hypothesis_metric", ""),
                    "expected_lift_pct": r.get("expected_lift_pct", ""),
                    "eval_window_days": r.get("eval_window_days", ""),
                    "decision": r.get("decision", "") or "DRAFT",
                })
    return {"releases": releases}


def main() -> int:
    print(f"Token: {TOKEN}")
    print(f"Output: {DATA_DIR}")
    client = bigquery.Client(project=PROJECT)

    summary = build_summary(client)
    write_json("summary.json", summary)
    write_json("funnel.json", build_funnel(client))
    write_json("channels.json", build_channels(client))
    write_json("utm_health.json", build_utm_health(client))
    write_json("releases.json", build_releases())
    update_archive(summary)
    print("refresh complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
