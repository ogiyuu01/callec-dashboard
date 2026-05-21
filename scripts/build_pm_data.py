#!/usr/bin/env python3
"""Build PM dashboard data (pm.json) from existing 4-source JSON.

Sources: Shopify (products/customers/shopify_metrics/goal) + GA4 (summary/funnel/
channels/utm_health) + GDrive (budget) + Klaviyo (klaviyo).

Writes d-*/data/pm.json with sections:
  - alerts (CRITICAL only)
  - scorecard (4 pillars + overall)
  - kpis (5 cards, SMART with target+period)
  - pipeline_health (4 sources freshness)
  - signals (Layer 2 business signals)
  - activity (time-ordered events from 4 sources)
"""
from __future__ import annotations
import glob
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIRS = sorted(ROOT.glob("d-*/data"))


def load(d: Path, name: str):
    p = d / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def parse_ts(s: str | None):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M JST", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s.replace("Z", ""), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            return dt
        except ValueError:
            continue
    return None


def hours_ago(dt: datetime | None, now: datetime):
    if dt is None:
        return None
    return (now - dt).total_seconds() / 3600.0


def light(score: float) -> str:
    if score >= 75:
        return "green"
    if score >= 50:
        return "yellow"
    return "red"


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def build_for(d: Path) -> dict:
    now = datetime.now(JST)
    summary = load(d, "summary.json") or {}
    funnel = load(d, "funnel.json") or {}
    channels = load(d, "channels.json") or {}
    channel_funnel = load(d, "channel_funnel.json") or {}
    utm = load(d, "utm_health.json") or {}
    products = load(d, "products.json") or {}
    customers = load(d, "customers.json") or {}
    shopify = load(d, "shopify_metrics.json") or {}
    goal = load(d, "goal.json") or {}
    budget = load(d, "budget.json") or {}
    klaviyo = load(d, "klaviyo.json") or {}

    # ---------- pipeline_health ----------
    ga4_dt = parse_ts(summary.get("last_updated"))
    shopify_dt = parse_ts((shopify.get("_meta") or {}).get("fetched_at"))
    gdrive_dt = parse_ts((budget.get("_meta") or {}).get("fetched_at"))
    klaviyo_dt = parse_ts(klaviyo.get("last_updated"))

    # 鮮度判定: cron は 09:00 JST 1日1回前提。
    # green=直近 cron 後 (今朝 09:00 JST 以降 or 今日中 or 24h以内のうち最も寛容)
    # yellow=昨日中 (48h以内)
    # red=それより前
    today_0900 = now.replace(hour=9, minute=0, second=0, microsecond=0)
    cron_window_start = today_0900 if now >= today_0900 else (today_0900 - timedelta(days=1))

    def health_row(name, dt):
        h = hours_ago(dt, now)
        if dt is None:
            return {
                "source": name, "status": "red", "last_run": "—",
                "lag_hours": None, "note": "未取得",
            }
        if dt >= cron_window_start:
            status, note = "green", f"{h:.1f}h前 (今朝以降)"
        elif h <= 48:
            status, note = "yellow", f"{h:.1f}h前 (昨日)"
        else:
            status, note = "red", f"{h:.1f}h前 (>48h)"
        return {
            "source": name,
            "status": status,
            "last_run": dt.strftime("%Y-%m-%d %H:%M"),
            "lag_hours": round(h, 1),
            "note": note,
        }

    pipeline = [
        health_row("GA4", ga4_dt),
        health_row("Shopify", shopify_dt),
        health_row("GDrive", gdrive_dt),
        health_row("Klaviyo", klaviyo_dt),
    ]

    # ---------- KPIs (Layer 1) ----------
    g_actual = (goal.get("actual") or {}).get("sales", 0) or 0
    g_target = (goal.get("target") or {}).get("sales", 0) or 0
    g_projected = (goal.get("projected") or {}).get("sales", 0) or 0
    g_progress = (goal.get("progress") or {}).get("sales_pct", 0) or 0
    g_landing_pct = (goal.get("progress") or {}).get("projected_sales_pct", 0) or 0
    g_remaining_daily = (goal.get("remaining") or {}).get("daily_needed_sales", 0) or 0
    g_month = goal.get("month", "—")
    g_days_remaining = goal.get("days_remaining", 0)

    last7 = summary.get("last_7d") or {}
    prev7 = summary.get("prev_7d") or {}
    cvr = last7.get("cvr") or 0
    aov = last7.get("aov") or 0
    aov_prev = prev7.get("aov") or 0
    aov_delta = ((aov - aov_prev) / aov_prev * 100) if aov_prev else 0

    repeat_rate = (shopify.get("customers_28d") or {}).get("returning_customer_rate", 0) or 0

    # Klaviyo attributable revenue (30d sum across flows)
    klaviyo_30d = 0.0
    for f in (klaviyo.get("flows") or []):
        klaviyo_30d += (f.get("current_30d") or {}).get("rev", 0) or 0

    def status_for(progress_pct, green=80, yellow=60):
        if progress_pct >= green:
            return "green"
        if progress_pct >= yellow:
            return "yellow"
        return "red"

    kpis = [
        {
            "label": "月次売上",
            "value": f"¥{g_actual:,.0f}",
            "sub": f"目標 ¥{g_target:,.0f} ({g_month})",
            "progress": round(g_progress, 1),
            "status": status_for(g_landing_pct),  # judge by landing
            "source": "Shopify",
            "target_meta": f"{g_month} 月次目標",
        },
        {
            "label": "着地予測",
            "value": f"¥{g_projected:,.0f}",
            "sub": f"進捗 {g_landing_pct:.0f}% / 残{g_days_remaining}日 / 日販 ¥{g_remaining_daily:,.0f} 必要",
            "progress": round(g_landing_pct, 1),
            "status": status_for(g_landing_pct),
            "source": "Shopify + GDrive",
            "target_meta": f"{g_month} 着地予測",
        },
        {
            "label": "Repeat率 (28日)",
            "value": f"{repeat_rate*100:.1f}%",
            "sub": "目標 ≥30.0% (FY26)",
            "progress": round(repeat_rate * 100 / 30 * 100, 1),
            "status": "green" if repeat_rate >= 0.30 else ("yellow" if repeat_rate >= 0.25 else "red"),
            "source": "Shopify",
            "target_meta": "FY26 通年目標",
        },
        {
            "label": "CVR (直近7日)",
            "value": f"{cvr*100:.2f}%",
            "sub": "目標 ≥0.70% (R001目標)",
            "progress": round(cvr * 100 / 0.70 * 100, 1) if cvr else 0,
            "status": "green" if cvr >= 0.007 else ("yellow" if cvr >= 0.005 else "red"),
            "source": "GA4 × Shopify",
            "target_meta": "R001 改善目標",
        },
        {
            "label": "Klaviyo 寄与売上 (30d)",
            "value": f"¥{klaviyo_30d:,.0f}",
            "sub": f"月次売上比 {(klaviyo_30d / g_actual * 100):.1f}%" if g_actual else "—",
            "progress": round(klaviyo_30d / g_actual * 100, 1) if g_actual else 0,
            "status": "green" if klaviyo_30d > 150000 else ("yellow" if klaviyo_30d > 80000 else "red"),
            "source": "Klaviyo",
            "target_meta": "目安 月¥150k+",
        },
    ]

    # ---------- 4 Pillar Scorecard ----------
    # Pillar 1: 着地 (40 pts weight)
    landing_score = clamp(g_landing_pct)
    # Pillar 2: トラフィック品質 (GA4 CVR + items/session) (20 pts)
    cvr_score = clamp(cvr * 100 / 0.70 * 100)
    items_ps = last7.get("items_per_session") or 0
    items_score = clamp(items_ps / 1.40 * 100)
    traffic_score = (cvr_score + items_score) / 2
    # Pillar 3: 配信ヘルス (Klaviyo) (20 pts)
    open_rates = [(f.get("current_30d") or {}).get("open_rate", 0) or 0 for f in (klaviyo.get("flows") or [])]
    avg_open = sum(open_rates) / len(open_rates) if open_rates else 0
    delivery_score = clamp(avg_open / 0.25 * 100)
    # Pillar 4: データ鮮度 (20 pts)
    fresh_map = {"green": 100, "yellow": 60, "red": 20}
    fresh_scores = [fresh_map.get(r["status"], 0) for r in pipeline]
    freshness_score = sum(fresh_scores) / len(fresh_scores) if fresh_scores else 0

    overall = landing_score * 0.4 + traffic_score * 0.2 + delivery_score * 0.2 + freshness_score * 0.2

    scorecard = {
        "overall_score": round(overall),
        "pillars": [
            {
                "name": "着地予測",
                "status": light(landing_score),
                "score": round(landing_score),
                "note": f"{g_landing_pct:.0f}% 着地見込み",
                "weight": 40,
            },
            {
                "name": "トラフィック品質",
                "status": light(traffic_score),
                "score": round(traffic_score),
                "note": f"CVR {cvr*100:.2f}% / items/session {items_ps:.2f}",
                "weight": 20,
            },
            {
                "name": "配信ヘルス (Klaviyo)",
                "status": light(delivery_score),
                "score": round(delivery_score),
                "note": f"平均開封率 {avg_open*100:.1f}% (tgt ≥25%)",
                "weight": 20,
            },
            {
                "name": "データ鮮度",
                "status": light(freshness_score),
                "score": round(freshness_score),
                "note": f"4ソース平均 {freshness_score:.0f}/100",
                "weight": 20,
            },
        ],
    }

    # ---------- Layer 2 Business Signals ----------
    signals = []
    # CVR
    signals.append({
        "metric": "CVR (7d)",
        "value": f"{cvr*100:.2f}%",
        "target": "≥0.70%",
        "status": "green" if cvr >= 0.007 else ("yellow" if cvr >= 0.005 else "red"),
        "source": "GA4 × Shopify",
    })
    # items/session
    signals.append({
        "metric": "items / session",
        "value": f"{items_ps:.2f}",
        "target": "≥1.40 (R001)",
        "status": "green" if items_ps >= 1.40 else ("yellow" if items_ps >= 1.20 else "red"),
        "source": "GA4",
    })
    # AOV
    signals.append({
        "metric": "AOV",
        "value": f"¥{aov:,.0f}",
        "target": f"WoW {aov_delta:+.1f}%",
        "status": "green" if aov_delta >= 0 else ("yellow" if aov_delta >= -5 else "red"),
        "source": "Shopify",
    })
    # Cart Abandonment (funnel-based)
    steps = {s.get("name"): s.get("count", 0) for s in (funnel.get("steps") or [])}
    atc = steps.get("add_to_cart", 0)
    purchase = steps.get("purchase", 0)
    abandon = (1 - purchase / atc) if atc else None
    if abandon is not None:
        signals.append({
            "metric": "Cart Abandonment",
            "value": f"{abandon*100:.1f}%",
            "target": "≤75% (業界 70-80%)",
            "status": "green" if abandon <= 0.75 else ("yellow" if abandon <= 0.85 else "red"),
            "source": "GA4 funnel",
        })
    # Klaviyo open rate (avg)
    signals.append({
        "metric": "Klaviyo 平均 open率 (30d)",
        "value": f"{avg_open*100:.1f}%",
        "target": "≥25% (alert <15%)",
        "status": "green" if avg_open >= 0.25 else ("yellow" if avg_open >= 0.15 else "red"),
        "source": "Klaviyo",
    })
    # UTM 破損
    broken = utm.get("broken") or []
    broken_sessions = sum((b.get("sessions") or 0) for b in broken)
    signals.append({
        "metric": "UTM 破損 sessions",
        "value": f"{len(broken)}src / {broken_sessions} sessions",
        "target": "≤30 sessions",
        "status": "green" if broken_sessions <= 30 else ("yellow" if broken_sessions <= 100 else "red"),
        "source": "GA4",
    })
    # 改善対象 SKU (PV高・カート低のみ — traffic はあるが conversion 悪い、実 actionable な改善余地)
    # 「流入弱」は long-tail として除外（ECで普通に発生する、即時アクションしにくい）
    state_counts = products.get("state_counts") or {}
    improve_target = state_counts.get("PV高・カート低") or 0
    signals.append({
        "metric": "改善対象 SKU (PV高ATC低)",
        "value": f"{improve_target} SKU",
        "target": "≤60 green / ≤120 yellow (PDP/コピー改善優先)",
        "status": "green" if improve_target <= 60 else ("yellow" if improve_target <= 120 else "red"),
        "source": "GA4 funnel × Shopify products",
    })

    # 真の在庫切れ (Shopify InventoryLevel.available — scripts/refresh_shopify_inventory.py が生成)
    inventory = load(d, "inventory.json") or {}
    oos_count = inventory.get("oos_count")
    low_stock_count = inventory.get("low_stock_count")
    if oos_count is not None:
        signals.append({
            "metric": "在庫切れ SKU (真値)",
            "value": f"{oos_count} OOS / {low_stock_count or 0} 在庫薄",
            "target": "OOS = 0",
            "status": "green" if oos_count == 0 else ("yellow" if oos_count <= 3 else "red"),
            "source": "Shopify InventoryLevel",
        })

    # ---------- Alert Bar (CRITICAL/HIGH only) ----------
    alerts = []
    for s in signals:
        if s["status"] == "red":
            alerts.append({
                "level": "critical",
                "title": s["metric"],
                "value": s["value"],
                "target": s["target"],
                "source": s["source"],
            })
    for row in pipeline:
        if row["status"] == "red":
            alerts.append({
                "level": "critical",
                "title": f"{row['source']} データ未取得",
                "value": row["note"],
                "target": "<26h",
                "source": row["source"],
            })

    # ---------- Activity feed (4 sources) ----------
    activity = []

    def add_act(ts: datetime | None, source, kind, title, level="info"):
        if ts is None:
            return
        activity.append({
            "ts": ts.strftime("%Y-%m-%d %H:%M"),
            "ts_iso": ts.isoformat(),
            "source": source,
            "type": kind,
            "title": title,
            "level": level,
        })

    add_act(ga4_dt, "GA4", "snapshot", f"GA4 weekly snapshot 取得 (sessions {last7.get('sessions','—')})")
    add_act(shopify_dt, "Shopify", "snapshot", f"Shopify 28日メトリクス更新 (Repeat率 {repeat_rate*100:.1f}%)")
    add_act(gdrive_dt, "GDrive", "sync", "年間計画シート fetched")
    add_act(klaviyo_dt, "Klaviyo", "refresh", f"flows {len(klaviyo.get('flows') or [])} 件 (寄与売上 ¥{klaviyo_30d:,.0f}/30d)")

    # Klaviyo alerts
    for a in (klaviyo.get("alerts") or []):
        ts = parse_ts(a.get("ts") or klaviyo.get("last_updated"))
        add_act(ts, "Klaviyo", "alert", a.get("message") or str(a), level="warning")

    # GA4 signals
    for sig in (summary.get("signals") or []):
        if (sig.get("level") or "") in ("yellow", "red"):
            ts = ga4_dt
            add_act(ts, "GA4", "signal", f"{sig.get('metric')}={sig.get('value')} (tgt {sig.get('target')})",
                    level=("warning" if sig.get("level") == "yellow" else "critical"))

    # GA4 anomalies
    for an in (summary.get("anomalies") or []):
        ts = parse_ts(an.get("date") or an.get("ts"))
        add_act(ts, "GA4", "anomaly", an.get("message") or an.get("note") or "異常検知", level="warning")

    activity.sort(key=lambda x: x.get("ts_iso", ""), reverse=True)

    # ---------- final pm.json ----------
    return {
        "_meta": {
            "generated_at": now.strftime("%Y-%m-%d %H:%M JST"),
            "sources": ["Shopify", "GA4", "GDrive", "Klaviyo"],
            "version": 1,
        },
        "last_updated": now.strftime("%Y-%m-%d %H:%M JST"),
        "alerts": alerts,
        "scorecard": scorecard,
        "kpis": kpis,
        "pipeline_health": pipeline,
        "signals": signals,
        "activity": activity[:40],
    }


def main():
    if not DATA_DIRS:
        print("[build_pm_data] no d-*/data dir found", file=sys.stderr)
        sys.exit(0)
    for d in DATA_DIRS:
        pm = build_for(d)
        out = d / "pm.json"
        out.write_text(json.dumps(pm, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[build_pm_data] wrote {out} (alerts={len(pm['alerts'])}, score={pm['scorecard']['overall_score']})")


if __name__ == "__main__":
    main()
