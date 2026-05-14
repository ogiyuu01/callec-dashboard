# CALL&RESPONSE Dashboard

EC 分析ダッシュボード（自動更新・Web公開）。

**URL（公開後）**: `https://<github-username>.github.io/callec-dashboard/d-63105cad1843507b740ebd51cc4cd0f5/`

トークン付きパスは「URLを知っている人だけ見られる」セミ非公開方式。
URL を Notion / iCloud Drive / ブックマークに貼って使う。

---

## 構成

```
callec-dashboard/
├── d-<TOKEN>/              # ダッシュボード本体（トークン付きディレクトリ）
│   ├── index.html          # メインHTML（4タブ）
│   ├── assets/
│   │   ├── style.css
│   │   └── dashboard.js
│   └── data/               # GitHub Actions が自動更新する JSON
│       ├── summary.json
│       ├── funnel.json
│       ├── channels.json
│       ├── utm_health.json
│       └── releases.json
├── scripts/
│   └── refresh_data.py     # BigQuery → JSON 生成
├── .github/workflows/
│   └── refresh.yml         # 毎日09:00 JST 自動実行
├── index.html              # ルート（トークンを知らない人は404）
└── .token                  # トークン保管（gitに含めるがURLそのもの）
```

---

## ページ構成（4タブ）

| タブ | 内容 |
|---|---|
| 📊 サマリ | KPIカード（WoW/YoY）・週次トレンド・判定シグナル |
| 🛒 ファネル | 5段ファネル・items/session推移・コレクション PDP到達率 |
| 📣 チャネル | チャネル別KPI・週次推移・utm破損検知 |
| 🚀 リリース | release_log.csv の一覧と判定 |

---

## デプロイ手順（初回・約10分）

### 1. GitHub に新規パブリックリポジトリを作成

リポ名（任意・推奨）: `callec-dashboard`
公開設定: **Public**

### 2. ローカルから push

```bash
cd /Users/oogiyuuhei/Desktop/shopify-callec/callec-dashboard
git init
git add .
git commit -m "feat: initial dashboard"
git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/callec-dashboard.git
git push -u origin main
```

### 3. GitHub Pages を有効化

- リポジトリ Settings → Pages
- Source: `Deploy from a branch`
- Branch: `main` / folder: `/ (root)`
- Save

数分後 `https://<YOUR_USERNAME>.github.io/callec-dashboard/d-63105cad1843507b740ebd51cc4cd0f5/` でアクセス可能になります。

### 4. GitHub Actions 用 secrets 設定

リポジトリ Settings → Secrets and variables → Actions → New repository secret

| Secret 名 | 値 |
|---|---|
| `GCP_SA_KEY` | BigQuery 読み取り権限のあるサービスアカウント JSON 全文 |
| `SIBLING_REPO_TOKEN` | （任意）shopify-ec-automation リポ参照用 Personal Access Token |

### 5. GCP サービスアカウント作成手順（GCP_SA_KEY 用）

1. https://console.cloud.google.com/iam-admin/serviceaccounts?project=gen-lang-client-0015689236
2. 「サービスアカウントを作成」
3. 名前: `dashboard-reader`
4. 権限付与:
   - BigQuery データ閲覧者 (`roles/bigquery.dataViewer`)
   - BigQuery ジョブユーザー (`roles/bigquery.jobUser`)
5. キー作成 → JSON ダウンロード
6. JSON ファイル全文を `GCP_SA_KEY` シークレットにコピペ

### 6. 動作確認

リポジトリ → Actions タブ → "Refresh dashboard data" → "Run workflow" → 手動実行
→ 成功すると `d-*/data/*.json` が自動更新・コミットされる

---

## ローカルでの開発・テスト

```bash
# データ更新（ADC認証必須）
cd /Users/oogiyuuhei/Desktop/shopify-callec/callec-dashboard
python3 scripts/refresh_data.py

# ブラウザでプレビュー
open d-63105cad1843507b740ebd51cc4cd0f5/index.html
```

---

## トークンが漏洩した時

`.token` ファイルを新しい値に書き換え + ディレクトリ名をリネーム:

```bash
NEW_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(16))")
mv d-<OLD_TOKEN> d-$NEW_TOKEN
echo $NEW_TOKEN > .token
git add . && git commit -m "chore: rotate token"
git push
```

旧URLは404になる。

---

## 関連

- 親リポジトリ: `shopify-ec-automation/`
- BQ ビュー: `gen-lang-client-0015689236.analytics_320051621.sessions_clean`
- 分析プレイブック: `shopify-ec-automation/docs/analysis_playbook.md`
