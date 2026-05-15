"""年間運用マスターカレンダー (Google Sheets) → data/budget.json 同期スクリプト.

シート ID: 1nTe9DK5CTYxbXCq9nHbydVltNBwe00iG7z7U3jLkpbQ
gid: 245220017
読込専用 (シート側を正とする)。

認証:
  GOOGLE_APPLICATION_CREDENTIALS=path/to/dashboard-reader.json をセット、または
  --credentials path/to/key.json を渡す。GitHub Actions では google-github-actions/auth が
  ADC をセットしてくれるので環境変数は自動で効く。

出力: d-<hash>/data/budget.json (dashboard 公開ディレクトリ)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Dashboard 内の d-<hash> ディレクトリを自動検出
_DATA_DIRS = sorted(ROOT.glob("d-*/data"))
if not _DATA_DIRS:
    raise SystemExit("d-*/data ディレクトリが見つからない")
BUDGET_JSON = _DATA_DIRS[0] / "budget.json"
SHEET_ID = "1nTe9DK5CTYxbXCq9nHbydVltNBwe00iG7z7U3jLkpbQ"
MONTHLY_SUMMARY_TAB = "12M"  # 月次サマリ (1月〜12月の予算/実績)
MONTH_TAB_NAMES = [str(m) for m in range(1, 13)]  # 各月の daily table + forecast block
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


def _open_sheet(credentials_path: str | None):
    import gspread
    from google.oauth2.service_account import Credentials

    cred_path = credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not cred_path:
        raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS env or --credentials が必要")

    creds = Credentials.from_service_account_file(cred_path, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)


def _to_int(s: str) -> int | None:
    if not s:
        return None
    cleaned = re.sub(r"[^\d\-]", "", str(s).replace(",", ""))
    if cleaned in ("", "-"):
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def fetch_tab_values(credentials_path: str | None, tab_title: str) -> list[list[str]]:
    spreadsheet = _open_sheet(credentials_path)
    for ws in spreadsheet.worksheets():
        if ws.title == tab_title:
            return ws.get_all_values()
    raise SystemExit(f"タブ {tab_title!r} が見つからない")


def parse_monthly_summary(values: list[list[str]], year: int = 2026) -> tuple[dict, dict]:
    """12M タブから年間サマリと月別予算/実績/前年を抽出.

    Returns (annual_dict, months_dict) where months keys are 'YYYY-MM'.
    """
    # 売上行を見つける: 最初の年間サマリブロック (l2='売上' l3='前年' の最初の登場)
    # シート全体には月別の同形ブロックが複数あるが、年間 13 ヶ月列 (合計+12ヶ月) を持つのは
    # 先頭のみ。それ以降は月別日次テーブル内の同形ブロックなので無視する.
    sales_rows: dict[str, list[str]] = {}
    in_sales_block = False
    found_first_block = False
    for row in values:
        if len(row) < 4:
            continue
        l1, l2, l3 = (row[0] or "").strip(), (row[1] or "").strip(), (row[2] or "").strip()
        if l2 == "売上" and l3 in ("前年", "予算", "実績"):
            if found_first_block:
                break  # 2 つめ以降のブロックは無視
            sales_rows[l3] = row
            in_sales_block = True
            continue
        if in_sales_block and l1 == "" and l2 == "" and l3 in ("前年", "予算", "実績"):
            sales_rows[l3] = row
            continue
        if in_sales_block and l2 and l2 != "売上":
            in_sales_block = False
            found_first_block = True

    if "予算" not in sales_rows:
        raise SystemExit("売上予算行が見つからない")

    annual_target = _to_int(sales_rows["予算"][3]) or 0
    annual_actual = _to_int(sales_rows.get("実績", [None] * 4)[3]) or 0
    annual_prev = _to_int(sales_rows.get("前年", [None] * 4)[3]) or 0

    months: dict[str, dict] = {}
    for m in range(1, 13):
        col = 3 + m  # 0:l1 1:l2 2:l3 3:annual 4:1月 ...
        key = f"{year:04d}-{m:02d}"
        months[key] = {
            "target": _to_int(sales_rows["予算"][col]) if col < len(sales_rows["予算"]) else None,
            "actual": _to_int(sales_rows["実績"][col]) if "実績" in sales_rows and col < len(sales_rows["実績"]) else None,
            "prev_year": _to_int(sales_rows["前年"][col]) if "前年" in sales_rows and col < len(sales_rows["前年"]) else None,
        }

    return (
        {"target": annual_target, "actual": annual_actual, "prev_year": annual_prev},
        months,
    )


def parse_month_tab(values: list[list[str]]) -> dict:
    """月別タブから着地見込みブロックを抽出.

    シート上の各月タブでは forecast ラベルが右端寄せ (col 30 前後) に
    `label | value` の対で配置されている. 各セルをスキャンし、ラベル文字列の
    右隣セルを値として拾う.
    """
    targets = {"日平均売上", "着地見込み", "経過日数", "残り日数", "目標までの日割り"}
    block: dict[str, int | None] = {}
    for row in values:
        for j, cell in enumerate(row):
            label = (cell or "").strip()
            if label in targets and j + 1 < len(row):
                block[label] = _to_int(row[j + 1])
    return block


def build_budget(credentials_path: str | None, year: int = 2026) -> dict:
    summary_values = fetch_tab_values(credentials_path, MONTHLY_SUMMARY_TAB)
    annual, months = parse_monthly_summary(summary_values, year=year)

    for m_str in MONTH_TAB_NAMES:
        m = int(m_str)
        key = f"{year:04d}-{m:02d}"
        if key not in months:
            continue
        try:
            tab_values = fetch_tab_values(credentials_path, m_str)
        except SystemExit:
            continue  # タブが無い月はスキップ
        block = parse_month_tab(tab_values)
        if block:
            months[key].update(
                {
                    "daily_avg_actual": block.get("日平均売上"),
                    "landing_forecast": block.get("着地見込み"),
                    "days_elapsed": block.get("経過日数"),
                    "days_remaining": block.get("残り日数"),
                    "daily_required_to_target": block.get("目標までの日割り"),
                }
            )

    return {
        "_meta": {
            "source_sheet_id": SHEET_ID,
            "source_sheet_title": "CALL EC_2026年度年間計画",
            "source_tabs": [MONTHLY_SUMMARY_TAB] + MONTH_TAB_NAMES,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "note": "12M タブから年間サマリ、各月タブ (1〜12) から着地見込みを取得。シート側を正、読込専用。",
        },
        "year": year,
        "annual": annual,
        "months": months,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", help="path to service account json")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    budget = build_budget(args.credentials, year=args.year)

    if args.dry_run:
        print(json.dumps(budget, ensure_ascii=False, indent=2))
        return

    BUDGET_JSON.parent.mkdir(parents=True, exist_ok=True)
    BUDGET_JSON.write_text(
        json.dumps(budget, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {BUDGET_JSON}")


if __name__ == "__main__":
    main()
