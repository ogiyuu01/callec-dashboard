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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

from google.cloud import bigquery

JST = timezone(timedelta(hours=9))


def now_jst() -> datetime:
    """Always returns JST time. GH Actions runs in UTC by default."""
    return datetime.now(JST)

ROOT = Path(__file__).resolve().parent.parent
TOKEN = (ROOT / ".token").read_text().strip()
DATA_DIR = ROOT / f"d-{TOKEN}" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

PROJECT = "gen-lang-client-0015689236"
DATASET = "analytics_320051621"

# CSV ソースを解決する。
# 1. ダッシュボード repo 直下 data/weekly_kpi.csv (vendor 同梱、GH Actions の正規ソース)
# 2. ローカル開発: shopify-inhouse/shopify-ec-automation 配下
# 3. 旧 path: ROOT.parent / shopify-ec-automation
# 4. 旧 path: ROOT / shopify-ec-automation (GH Actions の旧 checkout 互換)
def _resolve_sibling() -> Path:
    candidates = [
        ROOT,                                                 # vendor 同梱 (data/weekly_kpi.csv)
        ROOT.parent.parent / "shopify-inhouse" / "shopify-ec-automation",  # ローカル
        ROOT.parent / "shopify-ec-automation",                # ローカル: dashboard/ の隣
        ROOT / "shopify-ec-automation",                       # GH Actions 旧 checkout 互換
    ]
    for c in candidates:
        if (c / "data" / "weekly_kpi.csv").exists():
            return c
    return candidates[0]


SIBLING = _resolve_sibling()
def _find_data_file(name: str) -> Path:
    """SIBLING がvendored dashboard 内を指していて見つからない場合に、
    shopify-ec-automation の正規パスを探索する fallback."""
    primary = SIBLING / "data" / name
    if primary.exists():
        return primary
    for c in (
        ROOT.parent.parent / "shopify-inhouse" / "shopify-ec-automation" / "data" / name,
        ROOT.parent / "shopify-ec-automation" / "data" / name,
        ROOT / "shopify-ec-automation" / "data" / name,
    ):
        if c.exists():
            return c
    return primary  # 不在のまま返す (exists() で False になる)


RELEASE_LOG = _find_data_file("release_log.csv")
KPI_SNAPSHOTS = _find_data_file("kpi_snapshots.csv")
WEEKLY_KPI = SIBLING / "data" / "weekly_kpi.csv"
MONTHLY_TARGET = SIBLING / "data" / "monthly_target.json"  # 旧フォーマット (deprecated)
BUDGET_JSON_LOCAL = DATA_DIR / "budget.json"  # dashboard 自前 (sync_budget.py が書く)
BUDGET_JSON_SIBLING = SIBLING / "data" / "budget.json"  # sibling 側 (ローカル開発用)
SHOPIFY_METRICS_LOCAL = DATA_DIR / "shopify_metrics.json"  # dashboard 自前 (常駐)
SHOPIFY_METRICS_SIBLING = SIBLING / "data" / "shopify_metrics.json"  # sibling (ローカル開発)
REPORTS_DIR = SIBLING / "outputs" / "reports"
WEEKLY_THEMES = SIBLING / "data" / "weekly_themes.json"


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


def build_weekly_archive_from_csv() -> list[dict]:
    """weekly_kpi.csv の 2026-W10 (= 2026-03-02 週) 以降を週次アーカイブエントリに変換する。
    各週で前週比・前年同週比を計算し、簡易ナラティブを生成。"""
    entries: list[dict] = []
    if not WEEKLY_KPI.exists():
        return entries

    with WEEKLY_KPI.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    by_week = {r["week"]: r for r in rows if r.get("week")}

    for w_label, r in sorted(by_week.items()):
        if not w_label or "-W" not in w_label:
            continue
        try:
            y, wk = w_label.split("-W")
            monday = date.fromisocalendar(int(y), int(wk), 1)
        except Exception:
            continue
        if monday < date(2026, 3, 2):  # 2026-W10 = 2026-03-02 start (3月分から表示)
            continue

        sessions = int(r.get("sessions", 0) or 0)
        orders = int(r.get("orders", 0) or 0)
        sales = int(r.get("sales", 0) or 0)
        cvr = float(r.get("cvr", 0) or 0)
        aov = float(r.get("aov", 0) or 0)
        # CSV にある率から逆算 (atcs/checkouts/カゴ落ち率)。item_views は CSV にないので None
        atcs_rate = float(r.get("add_to_cart_rate", 0) or 0)
        ckout_rate = float(r.get("checkout_rate", 0) or 0)
        atcs = int(round(sessions * atcs_rate)) if sessions else 0
        checkouts = int(round(atcs * ckout_rate)) if atcs else 0
        cart_abandon = (atcs - orders) / atcs if atcs else 0

        # prev week
        try:
            prev_monday = monday - timedelta(days=7)
            py, pwk, _ = prev_monday.isocalendar()
            prev = by_week.get(f"{py}-W{pwk:02d}")
        except Exception:
            prev = None

        # YoY same week
        try:
            yoy_year = int(y) - 1
            yoy = by_week.get(f"{yoy_year}-W{wk}")
        except Exception:
            yoy = None

        # narrative
        lines = []
        if prev and int(prev.get("sales", 0) or 0):
            wow_pct = (sales - int(prev["sales"])) / int(prev["sales"]) * 100
            arrow = "📈" if wow_pct >= 5 else "📉" if wow_pct <= -5 else "→"
            lines.append(f"{arrow} 売上 {yen(sales)}（先週比 {wow_pct:+.0f}%）")
        else:
            lines.append(f"📊 売上 {yen(sales)}")

        if yoy and int(yoy.get("sales", 0) or 0):
            yoy_pct = (sales - int(yoy["sales"])) / int(yoy["sales"]) * 100
            sign = "✅" if yoy_pct >= 0 else "⚠️"
            lines.append(f"{sign} 前年同週比 {yoy_pct:+.0f}%（前年 {yen(int(yoy['sales']))}）")

        lines.append(f"   注文 {orders} 件・CVR {cvr*100:.2f}%・AOV {yen(aov)}")

        # closure caveat for 2025-W19/W20 (would never hit this block since we filter to 2026+ but safe)
        if w_label in ("2025-W19", "2025-W20"):
            lines.append("⚠️ 2025-05-07〜05-15 は閉店期間。数値は通常状態ではありません。")

        entries.append({
            "week": w_label,
            "start_date": monday.isoformat(),
            "end_date": (monday + timedelta(days=6)).isoformat(),
            "kpis": {
                "sessions": sessions, "orders": orders, "sales": sales,
                "cvr": cvr, "aov": aov,
                "item_views": None,  # CSV にない (28日以内なら update_archive で BQ から付与)
                "atcs": atcs, "checkouts": checkouts,
                "cart_abandonment_rate": cart_abandon,
                "items_per_session": None,
            },
            "prev": {"sessions": int(prev.get("sessions", 0) or 0), "orders": int(prev.get("orders", 0) or 0), "sales": int(prev.get("sales", 0) or 0)} if prev else {},
            "yoy":  {"sessions": int(yoy.get("sessions", 0) or 0),  "orders": int(yoy.get("orders", 0) or 0),  "sales": int(yoy.get("sales", 0) or 0)}  if yoy  else {},
            "narrative": {"lines": lines},
            "captured_at": now_jst().strftime("%Y-%m-%d %H:%M"),
        })

    entries.sort(key=lambda e: e["week"], reverse=True)
    return entries


def update_archive(summary: dict, client: "bigquery.Client | None" = None) -> None:
    """週次アーカイブ archive.json を生成する。
    1. weekly_kpi.csv ベースで 2026-W10 以降の確定週を全件展開
    2. 現在進行中の週は ISO週の月曜〜昨日を BQ から再集計して live 表示
       (summary.last_7d は rolling 7d なので週初の表示と合わない)
    """
    weekly_entries = build_weekly_archive_from_csv()

    # 現在進行中（=今週）のエントリは BQ から week-to-date を取得
    today = date.today()
    cur_monday = today - timedelta(days=today.weekday())
    iso_y, iso_w, _ = cur_monday.isocalendar()
    cur_label = f"{iso_y}-W{iso_w:02d}"
    cur_sunday = cur_monday + timedelta(days=6)
    last_day = today - timedelta(days=1)  # BQ にあるのは昨日まで

    def _query_week_kpis(start_d: date, end_d: date) -> dict | None:
        if client is None:
            return None
        try:
            sql = f"""
            SELECT COUNT(*) AS sessions,
                   SUM(item_views) AS item_views,
                   SUM(atcs) AS atcs,
                   SUM(checkouts) AS checkouts,
                   SUM(purchases) AS orders,
                   CAST(SUM(revenue) AS INT64) AS sales,
                   SAFE_DIVIDE(SUM(purchases), COUNT(*)) AS cvr,
                   SAFE_DIVIDE(SUM(revenue), NULLIF(SUM(purchases),0)) AS aov,
                   SAFE_DIVIDE(SUM(item_views), COUNT(*)) AS items_per_session
            FROM `{PROJECT}.{DATASET}.sessions_clean`
            WHERE event_date BETWEEN '{start_d.strftime("%Y%m%d")}' AND '{end_d.strftime("%Y%m%d")}'
            """
            r = list(client.query(sql).result())
            if not r:
                return None
            row = dict(r[0])
            atcs = int(row.get("atcs") or 0)
            orders = int(row.get("orders") or 0)
            return {
                "sessions": int(row.get("sessions") or 0),
                "item_views": int(row.get("item_views") or 0),
                "atcs": atcs,
                "checkouts": int(row.get("checkouts") or 0),
                "orders": orders,
                "sales": int(row.get("sales") or 0),
                "cvr": float(row.get("cvr") or 0),
                "aov": float(row.get("aov") or 0),
                "cart_abandonment_rate": (atcs - orders) / atcs if atcs else 0,
                "items_per_session": float(row.get("items_per_session") or 0),
            }
        except Exception as exc:
            print(f"  warn: BQ kpis query {start_d}〜{end_d} failed: {exc}")
            return None

    # 直近28日以内の閉じた週は BQ から item_views/atcs/checkouts を上書き取得
    bq_cutoff = today - timedelta(days=28)
    for e in weekly_entries:
        try:
            we = date.fromisoformat(e["end_date"])
            ws = date.fromisoformat(e["start_date"])
        except Exception:
            continue
        if we < bq_cutoff:
            continue
        end_q = min(we, last_day)
        if end_q < ws:
            continue
        bq_kpis = _query_week_kpis(ws, end_q)
        if not bq_kpis:
            continue
        # CSVに無い項目だけBQから入れる (sales/orders/cvr/aov は CSV 値を尊重)
        for k in ("item_views", "items_per_session"):
            e["kpis"][k] = bq_kpis.get(k)
        # atcs/checkouts/cart_abandonment_rate も BQ の方が正確なら上書き
        if bq_kpis.get("atcs"):
            e["kpis"]["atcs"] = bq_kpis["atcs"]
            e["kpis"]["checkouts"] = bq_kpis["checkouts"]
            csv_orders = e["kpis"].get("orders", 0)
            e["kpis"]["cart_abandonment_rate"] = (bq_kpis["atcs"] - csv_orders) / bq_kpis["atcs"]

    # live (今週) は BQ から week-to-date を取得
    live_kpis = summary.get("last_7d", {})  # フォールバック
    if last_day >= cur_monday:
        bq = _query_week_kpis(cur_monday, last_day)
        if bq:
            live_kpis = bq

    live_entry = {
        "week": cur_label,
        "start_date": cur_monday.isoformat(),
        "end_date": cur_sunday.isoformat(),
        "kpis": live_kpis,
        "yoy": summary.get("yoy", {}),
        "prev": summary.get("prev_7d", {}),
        # narrative は週途中で rolling 7d と week-to-date が混ざるため省略
        "captured_at": now_jst().strftime("%Y-%m-%d %H:%M"),
        "live": True,
        "live_through": last_day.isoformat(),  # 週途中の場合 何日までのデータか
    }

    # 重複排除
    weekly_entries = [e for e in weekly_entries if e.get("week") != cur_label]
    weekly_entries.append(live_entry)
    weekly_entries.sort(key=lambda w: w.get("week", ""), reverse=True)
    weekly_entries = weekly_entries[:60]  # 最大60週

    archive_path = DATA_DIR / "archive.json"
    archive_path.write_text(json.dumps({"weeks": weekly_entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  weekly archive: {len(weekly_entries)} weeks (from 2026-W10, live={cur_label})")


def build_monthly_archive() -> None:
    """weekly_kpi.csv を月単位に集計し、2026-03 以降の月次アーカイブを生成する。"""
    if not WEEKLY_KPI.exists():
        print("  monthly archive skipped (weekly_kpi.csv missing)")
        return

    from collections import defaultdict
    months: dict[str, dict] = defaultdict(lambda: {"sessions": 0.0, "orders": 0.0, "sales": 0.0})
    with WEEKLY_KPI.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            week = r.get("week", "")
            if not week or "-W" not in week:
                continue
            try:
                year, wk = week.split("-W")
                monday = date.fromisocalendar(int(year), int(wk), 1)
            except Exception:
                continue
            if monday < date(2025, 3, 1):  # 前年同月比較のため2025-03も含める
                continue
            sessions = int(r.get("sessions", 0) or 0)
            orders   = int(r.get("orders", 0) or 0)
            sales    = int(r.get("sales", 0) or 0)
            # 月跨ぎ週は1日均等(=1/7ずつ)で各月に按分し、境界月の片寄りを防ぐ
            for day_offset in range(7):
                d = monday + timedelta(days=day_offset)
                month_label = d.strftime("%Y-%m")
                months[month_label]["sessions"] += sessions / 7.0
                months[month_label]["orders"]   += orders / 7.0
                months[month_label]["sales"]    += sales / 7.0

    sorted_months = sorted(months.keys())
    entries: list[dict] = []
    for i, m in enumerate(sorted_months):
        if m < "2026-03":
            continue  # 表示は2026-03以降のみ
        v = months[m]
        # 1/7 按分で float 蓄積 → 表示・保存時に四捨五入
        sessions = int(round(v["sessions"]))
        orders = int(round(v["orders"]))
        sales = int(round(v["sales"]))
        cvr = orders / sessions if sessions else 0
        aov = sales / orders if orders else 0

        # MoM
        prev_m_idx = sorted_months.index(m) - 1
        prev = months[sorted_months[prev_m_idx]] if prev_m_idx >= 0 else None
        # YoY
        try:
            yoy_year = int(m[:4]) - 1
            yoy_label = f"{yoy_year}-{m[5:]}"
            yoy = months.get(yoy_label)
        except Exception:
            yoy = None

        narrative_lines = []
        arrow = "📈"
        if prev and prev["sales"]:
            mom_pct = (sales - prev["sales"]) / prev["sales"] * 100
            arrow = "📈" if mom_pct >= 5 else "📉" if mom_pct <= -5 else "→"
            narrative_lines.append(f"{arrow} 売上 {yen(sales)}（前月比 {mom_pct:+.0f}%）")
        else:
            narrative_lines.append(f"📊 売上 {yen(sales)}")

        if yoy and yoy["sales"]:
            yoy_pct = (sales - yoy["sales"]) / yoy["sales"] * 100
            sign = "✅" if yoy_pct >= 0 else "⚠️"
            narrative_lines.append(f"{sign} 前年同月比 {yoy_pct:+.0f}%（前年 {yen(yoy['sales'])}）")

        narrative_lines.append(f"   注文 {orders} 件・CVR {cvr*100:.2f}%・AOV {yen(aov)}")

        # 閉店期間注意（2025-05 は閉店週を含むため）
        if m == "2025-05":
            narrative_lines.append("⚠️ 2025-05-07〜05-15 は閉店期間。月次数値は通常状態ではありません。")

        entries.append({
            "month": m,
            "kpis": {"sessions": sessions, "orders": orders, "sales": sales, "cvr": cvr, "aov": aov},
            "prev": {"sessions": int(round(prev["sessions"])), "orders": int(round(prev["orders"])), "sales": int(round(prev["sales"]))} if prev else {},
            "yoy":  {"sessions": int(round(yoy["sessions"])),  "orders": int(round(yoy["orders"])),  "sales": int(round(yoy["sales"]))}  if yoy  else {},
            "narrative": {"lines": narrative_lines},
            "captured_at": now_jst().strftime("%Y-%m-%d %H:%M"),
        })

    entries.sort(key=lambda e: e["month"], reverse=True)
    out = DATA_DIR / "archive_monthly.json"
    out.write_text(json.dumps({"months": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  monthly archive: {len(entries)} months (from 2026-03)")


def q(client: bigquery.Client, sql: str) -> list[dict]:
    return [dict(r) for r in client.query(sql).result()]


def build_daily_series(client: bigquery.Client) -> dict:
    """直近28日の日次 KPI を返す（sparkline 用）。"""
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=27)
    sql = f"""
    SELECT
      event_date,
      COUNT(*) AS sessions,
      SUM(item_views) AS item_views,
      SUM(atcs) AS atcs,
      SUM(purchases) AS orders,
      CAST(SUM(revenue) AS INT64) AS sales,
      SAFE_DIVIDE(SUM(purchases), COUNT(*)) AS cvr,
      SAFE_DIVIDE(SUM(revenue), NULLIF(SUM(purchases),0)) AS aov,
      SAFE_DIVIDE(SUM(item_views), COUNT(*)) AS items_per_session
    FROM `{PROJECT}.{DATASET}.sessions_clean`
    WHERE event_date BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
    GROUP BY event_date
    ORDER BY event_date
    """
    rows = q(client, sql)
    days = [{
        "date": r["event_date"],
        "sessions": int(r.get("sessions") or 0),
        "item_views": int(r.get("item_views") or 0),
        "atcs": int(r.get("atcs") or 0),
        "orders": int(r.get("orders") or 0),
        "sales": int(r.get("sales") or 0),
        "cvr": float(r.get("cvr") or 0),
        "aov": float(r.get("aov") or 0),
        "items_per_session": float(r.get("items_per_session") or 0),
    } for r in rows]
    return {"days": days, "start_date": start.isoformat(), "end_date": end.isoformat()}


def build_products(client: bigquery.Client) -> dict:
    """商品別ファネル + 状態分類 (roadmap Phase 0)。
    直近28日（BQ蓄積範囲）で view→atc→buy を集計。
    """
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=27)
    sql = f"""
    SELECT
      it.item_name AS name,
      it.item_id AS sku,
      COUNTIF(event_name='view_item') AS views,
      COUNTIF(event_name='add_to_cart') AS atcs,
      COUNTIF(event_name='purchase') AS buys,
      CAST(SUM(IF(event_name='purchase', it.item_revenue, 0)) AS INT64) AS revenue,
      CAST(AVG(IF(event_name='view_item', it.price, NULL)) AS INT64) AS price
    FROM `{PROJECT}.{DATASET}.events_*`,
      UNNEST(items) AS it
    WHERE _TABLE_SUFFIX BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
      AND event_name IN ('view_item','add_to_cart','purchase')
      AND it.item_name IS NOT NULL
    GROUP BY name, sku
    HAVING views >= 1
    ORDER BY views DESC
    LIMIT 100
    """
    rows = q(client, sql)

    def classify(v, a, b):
        if v < 30: return ("流入弱", "露出を増やす：特集ページ・LINE・Instagram で見せる")
        atc_rate = (a / v) if v else 0
        buy_rate = (b / v) if v else 0
        atc_to_buy = (b / a) if a else 0
        if v >= 80 and atc_rate < 0.01:
            return ("PV高・カート低", "訴求改善：写真・コピー・商品魅力の言語化")
        if atc_rate >= 0.025 and atc_to_buy < 0.25 and a >= 4:
            return ("カート高・購入低", "不安解消：サイズ・配送・返品・在庫表示")
        if buy_rate >= 0.01 and v >= 50:
            return ("順調", "現状維持：露出継続")
        return ("経過観察", "もう少しデータを溜める")

    products = []
    for r in rows:
        v = int(r.get("views") or 0)
        a = int(r.get("atcs") or 0)
        b = int(r.get("buys") or 0)
        state, advice = classify(v, a, b)
        products.append({
            "name": r.get("name") or "—",
            "sku": r.get("sku") or "",
            "price": int(r.get("price") or 0),
            "views": v, "atcs": a, "buys": b,
            "revenue": int(r.get("revenue") or 0),
            "atc_per_view": round((a / v) * 100, 2) if v else 0,
            "buy_per_view": round((b / v) * 100, 2) if v else 0,
            "atc_to_buy": round((b / a) * 100, 1) if a else 0,
            "state": state,
            "advice": advice,
        })

    from collections import Counter
    state_counts = dict(Counter([p["state"] for p in products]))

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "products": products,
        "state_counts": state_counts,
    }


def build_products_top5(client: bigquery.Client) -> dict:
    """直近7日の購入金額 TOP5 商品。"""
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=6)
    sql = f"""
    SELECT
      it.item_name AS name,
      SUM(it.quantity) AS qty,
      CAST(SUM(it.item_revenue) AS INT64) AS revenue
    FROM `{PROJECT}.{DATASET}.events_*`,
      UNNEST(items) AS it
    WHERE _TABLE_SUFFIX BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
      AND event_name = 'purchase'
      AND it.item_name IS NOT NULL
    GROUP BY name
    ORDER BY revenue DESC
    LIMIT 5
    """
    rows = q(client, sql)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "items": [{
            "name": r["name"],
            "qty": int(r["qty"] or 0),
            "revenue": int(r["revenue"] or 0),
        } for r in rows],
    }


def build_anomalies(daily: dict) -> dict:
    """直近14日内で「異常な日」を検出する。
    各日について、直前 7 日移動平均との比 ±50% 以上、または特定の品質劣化日を拾う。"""
    days = daily.get("days", [])
    if len(days) < 14:
        return {"events": []}

    events = []
    METRICS = [
        ("sessions", "セッション"),
        ("orders", "注文"),
        ("sales", "売上"),
    ]
    # focus on last 14 days
    recent = days[-14:]
    for i, d in enumerate(recent):
        idx_in_full = len(days) - 14 + i
        if idx_in_full < 7:
            continue
        baseline = days[idx_in_full - 7: idx_in_full]
        for key, label in METRICS:
            base_avg = sum((b.get(key) or 0) for b in baseline) / 7
            cur = d.get(key) or 0
            if base_avg < 50:  # too small to compare
                continue
            delta_pct = (cur - base_avg) / base_avg * 100
            if abs(delta_pct) >= 50:
                direction = "up" if delta_pct > 0 else "down"
                events.append({
                    "date": d["date"],
                    "metric": label,
                    "value": cur,
                    "baseline_avg": int(base_avg),
                    "delta_pct": round(delta_pct, 0),
                    "direction": direction,
                })

    # newest first, cap
    events.sort(key=lambda e: e["date"], reverse=True)
    events = events[:12]

    # Format for display
    formatted = []
    for e in events:
        sign = "+" if e["delta_pct"] >= 0 else ""
        ymd = e["date"]
        date_short = f"{ymd[4:6]}/{ymd[6:8]}"
        desc = f"{e['metric']} が {e['value']:,} ({sign}{int(e['delta_pct'])}%・7日平均比)"
        formatted.append({
            "date": date_short,
            "iso_date": ymd,
            "desc": desc,
            "direction": e["direction"],
            "delta_text": f"{sign}{int(e['delta_pct'])}%",
        })
    return {"events": formatted}


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
        "last_updated": now_jst().strftime("%Y-%m-%d %H:%M JST"),
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


def build_products(client: bigquery.Client) -> dict:
    """商品別ファネル (view → atc → buy) を取得し、状態分類する。

    状態分類（apparel_ec_operation_roadmap.md より）:
    - PV高・カート低: views ≥ 中央値 AND atc/view < 1.5%
    - カート高・購入低: atc/view ≥ 2% AND buy/atc < 30%
    - 順調: その他で buy/view ≥ 1%
    - 流入弱: views < 中央値 AND atc/view 不問
    - 在庫情報は Shopify 連携後に追加（現状未連携のため除外）

    観測窓: 直近14日（5/1-5/14 等）。BQに事前に蓄積されている日付のみ。
    """
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=13)
    sql = f"""
    SELECT
      it.item_name AS name,
      it.item_id AS sku,
      COUNTIF(event_name='view_item') AS views,
      COUNTIF(event_name='add_to_cart') AS atcs,
      COUNTIF(event_name='purchase') AS buys,
      CAST(SUM(IF(event_name='purchase', it.item_revenue, 0)) AS INT64) AS revenue,
      ROUND(SAFE_DIVIDE(COUNTIF(event_name='add_to_cart'), NULLIF(COUNTIF(event_name='view_item'),0))*100, 2) AS atc_per_view,
      ROUND(SAFE_DIVIDE(COUNTIF(event_name='purchase'), NULLIF(COUNTIF(event_name='view_item'),0))*100, 2) AS buy_per_view,
      ROUND(SAFE_DIVIDE(COUNTIF(event_name='purchase'), NULLIF(COUNTIF(event_name='add_to_cart'),0))*100, 2) AS buy_per_atc
    FROM `{PROJECT}.{DATASET}.events_*`,
      UNNEST(items) AS it
    WHERE _TABLE_SUFFIX BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
      AND event_name IN ('view_item','add_to_cart','purchase')
      AND it.item_name IS NOT NULL
    GROUP BY name, sku
    HAVING views >= 10
    ORDER BY revenue DESC
    """
    rows = [dict(r) for r in client.query(sql).result()]
    if not rows:
        return {"period": {"start": start.isoformat(), "end": end.isoformat()}, "products": []}

    # 中央値計算
    views_list = sorted([int(r["views"] or 0) for r in rows])
    median_views = views_list[len(views_list)//2] if views_list else 0

    def classify(r: dict) -> dict:
        v = int(r["views"] or 0)
        a = int(r["atcs"] or 0)
        b = int(r["buys"] or 0)
        atc_per_view = float(r["atc_per_view"] or 0)  # %
        buy_per_view = float(r["buy_per_view"] or 0)
        buy_per_atc = float(r["buy_per_atc"] or 0)

        if v < median_views and v < 50:
            return {"state": "流入弱", "color": "muted", "advice": "露出強化（特集・LINE・Instagram）"}
        if atc_per_view < 1.5 and v >= median_views:
            return {"state": "PV高・カート低", "color": "warn", "advice": "写真・コピー・訴求改善（PDP改修候補）"}
        if atc_per_view >= 2.0 and buy_per_atc < 30 and a >= 3:
            return {"state": "カート高・購入低", "color": "warn", "advice": "サイズ・配送・返品の不安解消（CTA周辺改善）"}
        if buy_per_view >= 1.0:
            return {"state": "順調", "color": "good", "advice": "現状維持 + 在庫確認"}
        return {"state": "経過観察", "color": "neutral", "advice": "追加データ蓄積待ち"}

    products = []
    for r in rows:
        cls = classify(r)
        products.append({
            "name": r["name"] or "—",
            "sku": r["sku"] or "—",
            "views": int(r["views"] or 0),
            "atcs": int(r["atcs"] or 0),
            "buys": int(r["buys"] or 0),
            "revenue": int(r["revenue"] or 0),
            "atc_per_view": float(r["atc_per_view"] or 0),
            "buy_per_view": float(r["buy_per_view"] or 0),
            "buy_per_atc": float(r["buy_per_atc"] or 0),
            "state": cls["state"],
            "state_color": cls["color"],
            "advice": cls["advice"],
        })

    # 状態別集計
    from collections import Counter
    state_counts = dict(Counter(p["state"] for p in products))

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "median_views": median_views,
        "state_counts": state_counts,
        "products": products,
    }


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


def _metric_from_snapshot(metric: str, snap: dict) -> float | None:
    """hypothesis_metric 名から kpi_snapshots.csv の該当列を抽出。
    snapshot 列に直接対応しない metric (collection_to_pdp_rate / view_to_atc_rate) は None を返し、
    後段の BQ 計算 (_compute_metric_via_bq) に委ねる。"""
    if not snap:
        return None
    key_map = {
        "atc_to_checkout_rate": "checkout_rate",
        "items_per_session": "items_per_session",
        "cvr": "cvr",
    }
    col = key_map.get(metric)
    if not col:
        return None
    try:
        return float(snap.get(col) or 0)
    except (ValueError, TypeError):
        return None


def _compute_metric_via_bq(client: "bigquery.Client | None", metric: str, start: date, end: date) -> float | None:
    """snapshot に列がない metric を BQ から直接計算する。失敗時は None。"""
    if not client or not metric:
        return None
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    if metric == "collection_to_pdp_rate":
        sql = f"""
        WITH sess AS (
          SELECT
            CONCAT(user_pseudo_id, CAST((SELECT value.int_value FROM UNNEST(event_params) WHERE key='ga_session_id') AS STRING)) AS sess_id,
            MIN(IF(event_name='session_start', (SELECT value.string_value FROM UNNEST(event_params) WHERE key='page_location'), NULL)) AS landing,
            COUNTIF(event_name='view_item') AS item_views
          FROM `{PROJECT}.{DATASET}.events_*`
          WHERE _TABLE_SUFFIX BETWEEN '{s}' AND '{e}'
          GROUP BY sess_id
        )
        SELECT SAFE_DIVIDE(COUNTIF(item_views > 0), COUNT(*)) AS rate
        FROM sess WHERE REGEXP_CONTAINS(landing, r'/collections/')
        """
    elif metric == "view_to_atc_rate":
        sql = f"""
        SELECT SAFE_DIVIDE(COUNTIF(event_name='add_to_cart'), COUNTIF(event_name='view_item')) AS rate
        FROM `{PROJECT}.{DATASET}.events_*`
        WHERE _TABLE_SUFFIX BETWEEN '{s}' AND '{e}'
        """
    else:
        return None
    try:
        rows = list(client.query(sql).result())
        return float(rows[0].rate) if rows and rows[0].rate is not None else None
    except Exception as ex:
        print(f"  monitoring BQ query failed for {metric}: {ex}")
        return None


def _parse_decision_rule(rule: str) -> dict:
    """'GREEN: rate>=38% / RED: rate<=27%' から閾値辞書を抽出。失敗時は空辞書。"""
    import re
    result = {}
    for color in ("GREEN", "YELLOW", "RED"):
        m = re.search(rf"{color}[^/]*?(?:rate)?\s*([<>]=?)\s*(\d+(?:\.\d+)?)\s*%", rule)
        if m:
            result[color] = {"op": m.group(1), "value": float(m.group(2)) / 100.0}
    return result


def _verdict_color(rate: float | None, rule_dict: dict) -> str:
    if rate is None or not rule_dict:
        return "UNKNOWN"
    g = rule_dict.get("GREEN")
    r = rule_dict.get("RED")
    if g and ((g["op"] == ">=" and rate >= g["value"]) or (g["op"] == ">" and rate > g["value"])):
        return "GREEN"
    if r and ((r["op"] == "<=" and rate <= r["value"]) or (r["op"] == "<" and rate < r["value"])):
        return "RED"
    return "YELLOW"


def build_monitoring(client: "bigquery.Client | None" = None) -> dict:
    """release_log.csv の MONITORING 行 + kpi_snapshots.csv から監視ビューを構築。
    snapshot に列がない metric (collection_to_pdp_rate / view_to_atc_rate) は BQ で直接計算。"""
    if not RELEASE_LOG.exists():
        return {"releases": [], "_meta": {"generated_at": now_jst().isoformat(timespec="seconds")}}

    snapshots = []
    if KPI_SNAPSHOTS.exists():
        with KPI_SNAPSHOTS.open(encoding="utf-8-sig", newline="") as f:
            snapshots = list(csv.DictReader(f))

    today = date.today()
    out = []
    with RELEASE_LOG.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rid = (r.get("release_id") or "").strip()
            deployed_at = (r.get("deployed_at") or "").strip()
            decision = (r.get("decision") or "DRAFT").strip()
            if not rid or not deployed_at:
                continue
            try:
                d = datetime.fromisoformat(deployed_at).date()
            except ValueError:
                continue

            elapsed = (today - d).days
            eval_window = int(r.get("eval_window_days") or 14)
            metric = (r.get("hypothesis_metric") or "").strip()
            rule_str = (r.get("decision_rule") or "").strip()
            rule_dict = _parse_decision_rule(rule_str)

            rel_snaps = [s for s in snapshots if s.get("release_id") == rid]
            before = next((s for s in rel_snaps if s.get("trigger") == "release_before"), None)
            after_list = [s for s in rel_snaps if s.get("trigger") == "release_after"]
            after = max(after_list, key=lambda s: s.get("taken_at", ""), default=None)

            before_rate = _metric_from_snapshot(metric, before) if before else None
            after_rate = _metric_from_snapshot(metric, after) if after else None

            # BQ 直撃 fallback (snapshot 列なし metric 用)
            if before_rate is None and metric in ("collection_to_pdp_rate", "view_to_atc_rate"):
                before_start = d - timedelta(days=int(r.get("eval_window_days") or 7))
                before_end = d - timedelta(days=1)
                before_rate = _compute_metric_via_bq(client, metric, before_start, before_end)
            if after_rate is None and metric in ("collection_to_pdp_rate", "view_to_atc_rate"):
                if elapsed >= 1:
                    after_start = d
                    after_end = min(today - timedelta(days=1), d + timedelta(days=elapsed - 1))
                    after_rate = _compute_metric_via_bq(client, metric, after_start, after_end)
            lift_pct = None
            if before_rate and after_rate and before_rate > 0:
                lift_pct = round((after_rate - before_rate) / before_rate * 100, 1)

            verdict = _verdict_color(after_rate, rule_dict)

            out.append({
                "release_id": rid,
                "deployed_at": deployed_at,
                "days_elapsed": elapsed,
                "eval_window_days": eval_window,
                "progress_pct": min(100, round(elapsed / eval_window * 100)) if eval_window else 0,
                "hypothesis_metric": metric,
                "expected_lift_pct": r.get("expected_lift_pct") or "",
                "decision_rule": rule_str,
                "decision": decision,
                "verdict": verdict,
                "before_rate": before_rate,
                "after_rate": after_rate,
                "after_window": after.get("window") if after else "",
                "after_taken_at": after.get("taken_at") if after else "",
                "lift_pct": lift_pct,
                "summary": (r.get("summary") or "")[:200],
                "notes": (r.get("notes") or "")[:400],
            })
    return {
        "releases": out,
        "_meta": {
            "generated_at": now_jst().isoformat(timespec="seconds"),
            "source": "release_log.csv + kpi_snapshots.csv",
            "note": "collection_to_pdp_rate / view_to_atc_rate は snapshot 列直接対応がないため代理列または欠損。完全値は BQ 直撃 by snapshot_kpi.py を参照",
        },
    }


def build_goal_progress(client: bigquery.Client) -> dict:
    """月次目標と進捗・着地予測を返す（roadmap基準）。"""
    today = date.today()
    cur_month_str = today.strftime("%Y-%m")
    month_start = today.replace(day=1)
    days_in_month = (month_start.replace(month=month_start.month % 12 + 1, day=1) - timedelta(days=1)).day if month_start.month < 12 else 31
    days_passed = today.day
    days_remaining = days_in_month - days_passed

    # 月次目標読み込み: budget.json (dashboard 自前 → sibling) → 旧 monthly_target.json → デフォルト
    sales_target = 0
    orders_target = 200
    note = ""
    for budget_path in (BUDGET_JSON_LOCAL, BUDGET_JSON_SIBLING):
        if not budget_path.exists():
            continue
        try:
            budget = json.loads(budget_path.read_text(encoding="utf-8"))
            month_data = (budget.get("months") or {}).get(cur_month_str, {})
            sales_target = int(month_data.get("target") or 0)
            if sales_target:
                break
        except Exception:
            continue
    if not sales_target and MONTHLY_TARGET.exists():
        try:
            targets = json.loads(MONTHLY_TARGET.read_text(encoding="utf-8"))
            cur_target = (targets.get("targets") or {}).get(cur_month_str, {})
            sales_target = int(cur_target.get("sales") or targets.get("default_monthly_target") or 4000000)
            orders_target = int(cur_target.get("orders") or targets.get("default_orders_target") or 200)
            note = cur_target.get("note", "")
        except Exception:
            pass
    if not sales_target:
        sales_target = 4000000

    # MTD 実績（BQ）
    sql = f"""
    SELECT COUNT(*) AS sessions,
           SUM(purchases) AS orders,
           CAST(SUM(revenue) AS INT64) AS sales
    FROM `{PROJECT}.{DATASET}.sessions_clean`
    WHERE event_date BETWEEN '{month_start.strftime("%Y%m%d")}' AND '{(today - timedelta(days=1)).strftime("%Y%m%d")}'
    """
    rows = list(client.query(sql).result())
    actual = dict(rows[0]) if rows else {"sessions": 0, "orders": 0, "sales": 0}
    mtd_sales = int(actual.get("sales") or 0)
    mtd_orders = int(actual.get("orders") or 0)

    # 着地予測（線形外挿）
    pace_days = max(days_passed - 1, 1)  # 昨日まで
    daily_pace_sales = mtd_sales / pace_days if pace_days else 0
    daily_pace_orders = mtd_orders / pace_days if pace_days else 0
    projected_sales = int(daily_pace_sales * days_in_month)
    projected_orders = int(daily_pace_orders * days_in_month)

    return {
        "month": cur_month_str,
        "days_in_month": days_in_month,
        "days_passed": days_passed,
        "days_remaining": days_remaining,
        "target": {"sales": sales_target, "orders": orders_target, "note": note},
        "actual": {"sales": mtd_sales, "orders": mtd_orders},
        "projected": {"sales": projected_sales, "orders": projected_orders},
        "progress": {
            "sales_pct": round(mtd_sales / sales_target * 100, 1) if sales_target else 0,
            "orders_pct": round(mtd_orders / orders_target * 100, 1) if orders_target else 0,
            "projected_sales_pct": round(projected_sales / sales_target * 100, 1) if sales_target else 0,
            "projected_orders_pct": round(projected_orders / orders_target * 100, 1) if orders_target else 0,
        },
        "remaining": {
            "sales": max(sales_target - mtd_sales, 0),
            "orders": max(orders_target - mtd_orders, 0),
            "daily_needed_sales": int((sales_target - mtd_sales) / days_remaining) if days_remaining > 0 else 0,
            "daily_needed_orders": (orders_target - mtd_orders) / days_remaining if days_remaining > 0 else 0,
        },
    }


def build_customer_segments(client: bigquery.Client) -> dict:
    """新規 vs リピート の直近30日比較 + 推移。"""
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=29)

    sql = f"""
    SELECT
      CASE WHEN user_pseudo_id IN (
        SELECT DISTINCT user_pseudo_id FROM `{PROJECT}.{DATASET}.sessions_clean`
        WHERE event_date < '{start.strftime("%Y%m%d")}'
      ) THEN 'returning' ELSE 'new' END AS seg,
      COUNT(*) AS sessions,
      SUM(IF(atcs>0,1,0)) AS atc_sessions,
      SUM(IF(purchases>0,1,0)) AS buyer_sessions,
      SUM(purchases) AS orders,
      CAST(SUM(revenue) AS INT64) AS sales
    FROM `{PROJECT}.{DATASET}.sessions_clean`
    WHERE event_date BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
    GROUP BY seg
    """
    segments = {}
    try:
        for r in client.query(sql).result():
            d = dict(r)
            s = d["seg"]
            sess = int(d.get("sessions") or 0)
            segments[s] = {
                "sessions": sess,
                "atc_rate": round((d.get("atc_sessions") or 0) / sess * 100, 2) if sess else 0,
                "cvr": round((d.get("buyer_sessions") or 0) / sess * 100, 3) if sess else 0,
                "orders": int(d.get("orders") or 0),
                "sales": int(d.get("sales") or 0),
            }
    except Exception as e:
        return {"period": {"start": start.isoformat(), "end": end.isoformat()}, "segments": {}, "error": str(e)}

    total_sales = sum(s["sales"] for s in segments.values()) or 1
    for s in segments.values():
        s["sales_share"] = round(s["sales"] / total_sales * 100, 1)
    return {"period": {"start": start.isoformat(), "end": end.isoformat(), "days": 30}, "segments": segments}


def build_channel_funnel(client: bigquery.Client) -> dict:
    """チャネル別ファネル分解（直近14日）。各チャネルでどこで詰まっているか。"""
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=13)

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
    sql = f"""
    SELECT {case_channel},
      COUNT(*) AS sessions,
      SUM(IF(item_views>0,1,0)) AS pdp,
      SUM(IF(atcs>0,1,0)) AS atc,
      SUM(IF(checkouts>0,1,0)) AS co,
      SUM(IF(purchases>0,1,0)) AS buy
    FROM `{PROJECT}.{DATASET}.sessions_clean`
    WHERE event_date BETWEEN '{start.strftime("%Y%m%d")}' AND '{end.strftime("%Y%m%d")}'
    GROUP BY channel
    HAVING sessions >= 50
    ORDER BY sessions DESC
    """
    rows = []
    for r in client.query(sql).result():
        d = dict(r)
        sess = int(d.get("sessions") or 0)
        pdp = int(d.get("pdp") or 0)
        atc = int(d.get("atc") or 0)
        co = int(d.get("co") or 0)
        buy = int(d.get("buy") or 0)
        rows.append({
            "channel": d["channel"],
            "sessions": sess,
            "pdp": pdp, "atc": atc, "co": co, "buy": buy,
            "pdp_rate": round(pdp/sess*100, 1) if sess else 0,
            "atc_rate": round(atc/sess*100, 2) if sess else 0,
            "cvr": round(buy/sess*100, 3) if sess else 0,
        })
    return {"period": {"start": start.isoformat(), "end": end.isoformat()}, "channels": rows}


def build_dynamic_actions(summary: dict, goal: dict, products: dict, releases: list) -> list[str]:
    """状況から「今やるべきこと」を動的生成する。"""
    actions = []
    # 1. items/session が低い場合
    last7 = (summary or {}).get("last_7d") or {}
    ips = last7.get("items_per_session") or 0
    if ips and ips < 1.2:
        actions.append(f"items/session {ips:.2f} が低水準。コレクションページの PDP 誘導施策を進める")
    # 2. 目標達成ペース
    proj_pct = ((goal or {}).get("progress") or {}).get("projected_sales_pct") or 0
    if proj_pct and proj_pct < 90:
        rem_daily = ((goal or {}).get("remaining") or {}).get("daily_needed_sales") or 0
        actions.append(f"月末着地予測 {proj_pct:.0f}% (未達)。残り日 1日あたり ¥{rem_daily:,} の売上が必要")
    elif proj_pct and proj_pct >= 110:
        actions.append(f"月末着地予測 {proj_pct:.0f}% (上振れ)。来月目標を上方修正検討")
    # 3. PDP改修候補が多い
    state_counts = (products or {}).get("state_counts") or {}
    pdp_fix = state_counts.get("PV高・カート低") or 0
    if pdp_fix >= 10:
        actions.append(f"PDP改修候補が {pdp_fix} 商品。上位5商品の写真/コピー/サイズ訴求を改善")
    cart_fix = state_counts.get("カート高・購入低") or 0
    if cart_fix >= 3:
        actions.append(f"カート高購入低が {cart_fix} 商品。配送・返品・サイズ不安の解消を CTA 周辺に追加")
    # 4. 進行中 DRAFT リリース
    drafts = [r for r in (releases or []) if r.get("decision") == "DRAFT" and not r.get("deployed_at")]
    if drafts:
        actions.append(f"未反映の DRAFT リリースが {len(drafts)} 件。理由整理 or 反映判断を進める")
    # 5. CVR 0.7% 未満なら集客抑制
    cvr = (last7.get("cvr") or 0) * 100
    if cvr and cvr < 0.7:
        actions.append(f"CVR {cvr:.2f}% は新規獲得を増やす段階ではない。CRO に集中")
    # フォールバック
    if not actions:
        actions.append("特に異常なし。週次テーマに沿った通常運用を継続")
    return actions[:6]


def build_reports_index() -> dict:
    """outputs/reports/*.md を一覧として返す。"""
    items = []
    if REPORTS_DIR.exists():
        for p in sorted(REPORTS_DIR.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            title = ""
            for line in text.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            # tag 推定
            name = p.name.lower()
            if name.startswith("monthly"):
                category = "月次"
            elif name.startswith("summary"):
                category = "月次"
            elif name.startswith("baseline"):
                category = "分析"
            elif name.startswith("exec"):
                category = "経営"
            elif name.startswith("findings"):
                category = "分析"
            elif "post_deployment" in name or "release" in name:
                category = "施策"
            else:
                category = "その他"
            items.append({
                "filename": p.name,
                "title": title or p.stem,
                "category": category,
                "size_bytes": p.stat().st_size,
                "modified_at": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "preview": text[:280],
                "body": text,
            })
    items.sort(key=lambda x: x["modified_at"], reverse=True)
    return {"reports": items, "count": len(items)}


def main() -> int:
    print(f"Token: {TOKEN}")
    print(f"Output: {DATA_DIR}")
    client = bigquery.Client(project=PROJECT)

    # ---- Build products (商品別ファネル + 状態分類) ----
    products_data = build_products(client)
    # Shopify実購入データをマージ (dashboard 自前 → sibling の順でフォールバック)
    for shopify_path in (SHOPIFY_METRICS_LOCAL, SHOPIFY_METRICS_SIBLING):
        if not shopify_path.exists():
            continue
        try:
            shopify = json.loads(shopify_path.read_text(encoding="utf-8"))
            products_data["shopify_top_28d"] = shopify.get("top_products_28d", [])
            products_data["shopify_customers_28d"] = shopify.get("customers_28d", {})
            products_data["shopify_meta"] = shopify.get("_meta", {})
            print(f"  loaded shopify_metrics from {shopify_path.name}")
            break
        except Exception as e:
            print(f"  shopify_metrics load failed ({shopify_path}): {e}")
    write_json("products.json", products_data)

    summary = build_summary(client)
    daily = build_daily_series(client)
    summary["daily"] = daily.get("days", [])
    summary["anomalies"] = build_anomalies(daily).get("events", [])

    # ---- Goal progress (月次目標 + 着地予測) ----
    goal = build_goal_progress(client)
    summary["goal"] = goal
    write_json("goal.json", goal)

    # ---- Channel funnel ----
    write_json("channel_funnel.json", build_channel_funnel(client))

    write_json("summary.json", summary)
    funnel_data = build_funnel(client)
    funnel_data["products_top5"] = build_products_top5(client).get("items", [])
    write_json("funnel.json", funnel_data)
    write_json("channels.json", build_channels(client))
    write_json("monitoring.json", build_monitoring(client))
    update_archive(summary, client)
    build_monthly_archive()
    print("refresh complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
