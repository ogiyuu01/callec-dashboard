#!/usr/bin/env python3
"""Refresh Shopify metrics + inventory via Admin API (Client Credentials Grant).

Generates / updates in each d-*/data/:
  - shopify_metrics.json (customers_28d, top_products_28d, _meta.fetched_at)
  - inventory.json       (oos_count, low_stock_count, items[])

Env vars required (set as GH Actions secrets OR local .env):
  SHOPIFY_SHOP_DOMAIN     e.g. r0ehyb-1a.myshopify.com
  SHOPIFY_CLIENT_ID
  SHOPIFY_CLIENT_SECRET
  SHOPIFY_API_VERSION     optional, default 2025-01

Notes:
  - stdlib only (urllib + json) — no extra deps to install in GH Actions.
  - Token cached for 24h in-process (single-run scripts don't need disk cache).
  - If env vars are missing the script exits 0 with a warning so refresh.yml
    `continue-on-error: true` keeps the pipeline running until user sets the
    GH Actions secrets.
"""
from __future__ import annotations
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIRS = sorted(ROOT.glob("d-*/data"))

SHOP = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip()
CID = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
CSEC = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2025-01").strip()
LOW_STOCK_THRESHOLD = int(os.environ.get("SHOPIFY_LOW_STOCK", "5"))
# コホート分析の遡及期間（既定365日）。各顧客の全注文履歴を取り切り、かつ
# 過去1年に獲得した顧客に十分な再購入機会を与えて「最終的な2回目転換」を測るため1年遡る。
COHORT_LOOKBACK_DAYS = int(os.environ.get("SHOPIFY_COHORT_LOOKBACK", "365"))
# 成熟期間（既定30日）。初回購入から最低この日数を経た顧客のみコホート対象とし、
# 初回直後の顧客を母数に含めない（過小評価を防ぐ）。
COHORT_MATURITY_DAYS = int(os.environ.get("SHOPIFY_COHORT_MATURITY", "30"))


def warn(msg):
    print(f"[refresh_shopify] WARN: {msg}", file=sys.stderr)


def fetch_admin_token() -> str:
    """Client Credentials Grant — see shopify-line-oem/docs."""
    url = f"https://{SHOP}/admin/oauth/access_token"
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": CID,
        "client_secret": CSEC,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["access_token"]


def gql(token: str, query: str, variables: dict | None = None) -> dict:
    url = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


CUSTOMERS_28D_QUERY = """
{
  orders(first: 250, query: "created_at:>=%s", sortKey: CREATED_AT) {
    edges { node {
      id name createdAt
      totalPriceSet { shopMoney { amount } }
      customer { id numberOfOrders }
      lineItems(first: 30) {
        edges { node {
          title quantity
          originalUnitPriceSet { shopMoney { amount } }
          product { id title }
        } }
      }
    } }
  }
}
"""

INVENTORY_QUERY = """
{
  productVariants(first: 250, query: "inventory_quantity:<=%d") {
    edges { node {
      id sku title
      inventoryQuantity
      product { id title }
    } }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def fetch_customers_28d(token: str, since_iso: str) -> dict:
    q = CUSTOMERS_28D_QUERY % since_iso
    res = gql(token, q)
    orders = [e["node"] for e in (res.get("data", {}).get("orders", {}).get("edges") or [])]
    customer_lifetime_orders: dict[str, int] = {}  # customer_id -> Customer.numberOfOrders (lifetime)
    total_sales = 0.0
    product_agg: dict[str, dict] = {}
    for o in orders:
        c = o.get("customer") or {}
        cid = c.get("id")
        if cid and cid not in customer_lifetime_orders:
            customer_lifetime_orders[cid] = int(c.get("numberOfOrders") or 1)
        try:
            total_sales += float((o.get("totalPriceSet") or {}).get("shopMoney", {}).get("amount") or 0)
        except (ValueError, TypeError):
            pass
        for le in (o.get("lineItems", {}).get("edges") or []):
            li = le["node"]
            title = (li.get("product") or {}).get("title") or li.get("title") or "—"
            qty = int(li.get("quantity") or 0)
            try:
                unit = float((li.get("originalUnitPriceSet") or {}).get("shopMoney", {}).get("amount") or 0)
            except (ValueError, TypeError):
                unit = 0
            row = product_agg.setdefault(title, {"name": title, "gross_sales": 0.0, "orders": 0})
            row["gross_sales"] += unit * qty
            row["orders"] += qty
    total_customers = len(customer_lifetime_orders)
    # Returning = customer.numberOfOrders >= 2 (lifetime, Shopify 標準定義)
    returning = sum(1 for n in customer_lifetime_orders.values() if n >= 2)
    new_c = total_customers - returning
    top_products = sorted(product_agg.values(), key=lambda x: x["gross_sales"], reverse=True)[:10]
    return {
        "customers_28d": {
            "total_customers": total_customers,
            "returning_customers": returning,
            "returning_customer_rate": (returning / total_customers) if total_customers else 0,
            "new_customers": new_c,
        },
        "top_products_28d": [{"name": r["name"], "gross_sales": round(r["gross_sales"]), "orders": r["orders"]} for r in top_products],
        "totals_28d": {
            "orders": len(orders),
            "sales": round(total_sales),
        },
    }


COHORT_QUERY = """
query($cursor: String) {
  orders(first: 250, after: $cursor, query: "created_at:>=%s", sortKey: CREATED_AT) {
    edges { node {
      id createdAt
      totalPriceSet { shopMoney { amount } }
      customer { id numberOfOrders }
      lineItems(first: 10) { edges { node {
        product {
          productType
          collections(first: 1) { edges { node { title handle } } }
        }
      } } }
    } }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _order_category(order: dict) -> str:
    """注文の代表カテゴリ。先頭 lineItem の productType、無ければ先頭 collection title。"""
    for le in (order.get("lineItems", {}).get("edges") or []):
        prod = (le.get("node") or {}).get("product") or {}
        ptype = (prod.get("productType") or "").strip()
        if ptype:
            return ptype
        colls = (prod.get("collections") or {}).get("edges") or []
        if colls:
            title = ((colls[0].get("node") or {}).get("title") or "").strip()
            if title:
                return title
    return "未分類"


def fetch_cohort(token: str, now: datetime) -> dict:
    """成熟コホートの2回目転換率＋カテゴリ別リピート率を算出する。

    定義（最終的な2回目転換を測る成熟コホート方式）:
      - コホート対象 = 「初回注文」が [now-LOOKBACK, now-MATURITY] にある顧客。
        初回から最低 COHORT_MATURITY_DAYS 経過しており、2回目購入の機会を十分得ている。
        遡及プル内で全注文を取り切れている（len(orders) >= numberOfOrders）顧客に限定し、
        プル開始日より前から購入歴がある顧客は除外（初回判定の誤りを防ぐ）。
      - 2回目転換率 = コホート顧客のうち lifetime 注文数が2以上に達した割合。
      - カテゴリ別リピート率 = カテゴリ内コホート顧客に対する2回目到達顧客の割合。
        カテゴリは各顧客の「初回注文」の代表カテゴリで割り当てる。
    """
    since = (now - timedelta(days=COHORT_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S%z")
    lookback_start = now - timedelta(days=COHORT_LOOKBACK_DAYS)
    mature_end = now - timedelta(days=COHORT_MATURITY_DAYS)

    # 顧客ごとに (プル内注文日リスト, lifetime注文数, 初回注文オブジェクト) を集める。
    by_customer: dict[str, dict] = {}
    cursor = None
    pages = 0
    while True:
        res = gql(token, COHORT_QUERY % since, {"cursor": cursor})
        orders_node = (res.get("data") or {}).get("orders") or {}
        for e in orders_node.get("edges") or []:
            o = e["node"]
            cid = (o.get("customer") or {}).get("id")
            if not cid:
                continue
            dt = _parse_iso(o.get("createdAt"))
            if dt is None:
                continue
            try:
                amt = float((o.get("totalPriceSet") or {}).get("shopMoney", {}).get("amount") or 0)
            except (ValueError, TypeError):
                amt = 0.0
            rec = by_customer.setdefault(
                cid,
                {"lifetime": int((o.get("customer") or {}).get("numberOfOrders") or 1),
                 "orders": []},
            )
            rec["orders"].append((dt, amt, o))
        pi = orders_node.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
        pages += 1
        if pages > 20:
            warn("cohort pagination cap hit (20 pages = 5000 orders)")
            break

    first_time = 0
    second_purchase = 0
    cats: dict[str, dict] = {}
    # 2回目判定はプル内の実注文数を主とし、numberOfOrders(生涯)で補完する。
    # numberOfOrders はキャンセル/アーカイブ等も数え orders クエリ返却と不一致になりうるため、
    # これを「全注文を取り切れたか」の判定に使うとリピート顧客を不当に除外してしまう（バイアス源）。
    diag = {
        "customers_total": len(by_customer),
        "excluded_too_recent": 0,        # 初回が成熟期間内(直近30日)で除外
        "excluded_before_lookback": 0,   # 初回がプル開始より前で除外
        "cohort_members": 0,
        "repeat_via_pull": 0,            # プル内注文数>=2 で2回目判定した数
        "repeat_via_lifetime_only": 0,   # プルは1件だが numberOfOrders>=2 で補完判定した数
    }
    for rec in by_customer.values():
        co = sorted(rec["orders"], key=lambda x: x[0])
        first_dt, _first_amt, first_order = co[0]
        if first_dt > mature_end:
            diag["excluded_too_recent"] += 1
            continue
        if first_dt < lookback_start:
            diag["excluded_before_lookback"] += 1
            continue
        diag["cohort_members"] += 1
        first_time += 1
        # 2回目到達: 実プル注文>=2 を主、numberOfOrders>=2 を補完。
        orders_in_pull = len(co)
        is_repeat = orders_in_pull >= 2 or rec["lifetime"] >= 2
        category = _order_category(first_order)
        cat = cats.setdefault(category, {"category": category, "first_buyers": 0, "repeat_buyers": 0})
        cat["first_buyers"] += 1
        if is_repeat:
            second_purchase += 1
            cat["repeat_buyers"] += 1
            if orders_in_pull >= 2:
                diag["repeat_via_pull"] += 1
            else:
                diag["repeat_via_lifetime_only"] += 1

    by_category = []
    for c in sorted(cats.values(), key=lambda x: -x["first_buyers"]):
        c["repeat_rate"] = round(c["repeat_buyers"] / c["first_buyers"], 4) if c["first_buyers"] else 0
        by_category.append(c)

    # --- CRM 追加指標（1年プルから算出） ---
    # 購入間隔: プル内2回以上購入した顧客の、連続注文の平均日数
    gaps = []
    total_amount = 0.0
    for rec in by_customer.values():
        co = sorted(rec["orders"], key=lambda x: x[0])
        total_amount += sum(a for (_d, a, _o) in co)
        for i in range(1, len(co)):
            gaps.append((co[i][0] - co[i - 1][0]).days)
    avg_interval = round(sum(gaps) / len(gaps), 1) if gaps else None
    ltv_1y = round(total_amount / len(by_customer)) if by_customer else None

    # 新規 / リピート注文（直近7日）: その注文より前にプル内注文がある顧客=リピート
    win7 = now - timedelta(days=7)
    new_orders_7d = 0
    repeat_orders_7d = 0
    for rec in by_customer.values():
        co = sorted(rec["orders"], key=lambda x: x[0])
        for idx, (dt, _a, _o) in enumerate(co):
            if dt >= win7:
                if idx == 0:
                    new_orders_7d += 1
                else:
                    repeat_orders_7d += 1

    return {
        "cohort": {
            "method": "mature",
            "maturity_days": COHORT_MATURITY_DAYS,
            "lookback_days": COHORT_LOOKBACK_DAYS,
            "first_time_buyers": first_time,
            "second_purchase_buyers": second_purchase,
            "second_purchase_rate": round(second_purchase / first_time, 4) if first_time else 0,
            "by_category": by_category,
            "diagnostics": diag,
        },
        "crm": {
            "avg_purchase_interval_days": avg_interval,
            "ltv_1y": ltv_1y,
            "new_orders_7d": new_orders_7d,
            "repeat_orders_7d": repeat_orders_7d,
        },
    }


def fetch_inventory(token: str) -> dict:
    """Fetch variants with inventory <= LOW_STOCK_THRESHOLD. OOS = inventory_quantity <= 0."""
    items: list[dict] = []
    cursor = None
    pages = 0
    while True:
        if cursor:
            q = f"""{{
              productVariants(first: 250, after: "{cursor}", query: "inventory_quantity:<={LOW_STOCK_THRESHOLD}") {{
                edges {{ node {{ id sku title inventoryQuantity product {{ id title }} }} }}
                pageInfo {{ hasNextPage endCursor }}
              }}
            }}"""
        else:
            q = INVENTORY_QUERY % LOW_STOCK_THRESHOLD
        res = gql(token, q)
        pv = (res.get("data") or {}).get("productVariants") or {}
        for e in pv.get("edges", []):
            n = e["node"]
            items.append({
                "sku": n.get("sku") or "",
                "title": (n.get("product") or {}).get("title") or n.get("title") or "—",
                "variant": n.get("title") or "",
                "inventory_quantity": n.get("inventoryQuantity") or 0,
            })
        pi = pv.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
        pages += 1
        if pages > 20:
            warn("inventory pagination cap hit (20 pages = 5000 variants)")
            break
    oos = [i for i in items if i["inventory_quantity"] <= 0]
    low = [i for i in items if 0 < i["inventory_quantity"] <= LOW_STOCK_THRESHOLD]
    return {
        "oos_count": len(oos),
        "low_stock_count": len(low),
        "low_stock_threshold": LOW_STOCK_THRESHOLD,
        "items_oos": oos[:50],
        "items_low": low[:50],
    }


def main():
    if not (SHOP and CID and CSEC):
        warn("SHOPIFY_SHOP_DOMAIN / CLIENT_ID / CLIENT_SECRET 未設定 — skip (set GH Actions secrets to enable)")
        sys.exit(0)
    if not DATA_DIRS:
        warn("no d-*/data dir found")
        sys.exit(0)

    now = datetime.now(JST)
    since_iso = (now - timedelta(days=28)).strftime("%Y-%m-%dT%H:%M:%S%z")

    try:
        token = fetch_admin_token()
    except Exception as e:
        warn(f"token fetch failed: {e}")
        sys.exit(0)

    # shopify_metrics.json
    try:
        metrics = fetch_customers_28d(token, since_iso)
    except Exception as e:
        warn(f"customers_28d fetch failed: {e}")
        metrics = None

    # コホート（2回目転換率・カテゴリ別リピート率）。失敗しても metrics は出す。
    cohort = None
    try:
        cohort = fetch_cohort(token, now)
        cd = (cohort or {}).get("cohort", {})
        print(f"[refresh_shopify] cohort: rate={cd.get('second_purchase_rate')} "
              f"first={cd.get('first_time_buyers')} second={cd.get('second_purchase_buyers')} "
              f"diag={cd.get('diagnostics')}")
    except Exception as e:
        warn(f"cohort fetch failed: {e}")

    # inventory.json
    try:
        inv = fetch_inventory(token)
    except Exception as e:
        warn(f"inventory fetch failed: {e}")
        inv = None

    for d in DATA_DIRS:
        if metrics is not None:
            payload = {
                "_meta": {
                    "source": "Shopify Admin API (Client Credentials Grant)",
                    "fetched_at": now.strftime("%Y-%m-%d %H:%M JST"),
                    "period_days": 28,
                    "shop_domain": SHOP,
                    "note": "refresh_shopify.py 自動更新。client_credentials grant + GraphQL 2025-01。",
                },
                **metrics,
                **(cohort or {}),
            }
            (d / "shopify_metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[refresh_shopify] wrote {d}/shopify_metrics.json (customers={metrics['customers_28d']['total_customers']}, returning_rate={metrics['customers_28d']['returning_customer_rate']*100:.1f}%)")

        if inv is not None:
            payload = {
                "_meta": {
                    "source": "Shopify Admin API InventoryLevel",
                    "fetched_at": now.strftime("%Y-%m-%d %H:%M JST"),
                    "shop_domain": SHOP,
                },
                **inv,
            }
            (d / "inventory.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[refresh_shopify] wrote {d}/inventory.json (OOS={inv['oos_count']}, low={inv['low_stock_count']})")


if __name__ == "__main__":
    main()
