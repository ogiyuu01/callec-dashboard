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
      customer { id }
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
    customers_ids = set()
    customer_orders_count: dict[str, int] = {}
    total_sales = 0.0
    product_agg: dict[str, dict] = {}
    for o in orders:
        cid = (o.get("customer") or {}).get("id")
        if cid:
            customers_ids.add(cid)
            customer_orders_count[cid] = customer_orders_count.get(cid, 0) + 1
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
    total_customers = len(customers_ids)
    returning = sum(1 for n in customer_orders_count.values() if n >= 2)
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
