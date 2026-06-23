# RichMenu Proxy — LINE API 中継サーバー

管理ページ（ブラウザ）から LINE Messaging API を呼ぶための中継サーバーです。
ブラウザは `api.line.me` を直接呼べない（CORS）ため、これを経由します。

## 仕組み
- 店舗ごとにトークンが違うので、管理ページが `Authorization: Bearer <token>` を送り、
  プロキシはそれを**そのまま LINE に転送**します（サーバーにトークンを保存しません）。
- 誰でも叩けないよう `X-Proxy-Key`（合言葉）での簡易認証を付けています。

## ファイル
| ファイル | 役割 |
|---|---|
| `app.py` | プロキシ本体（Flask） |
| `requirements.txt` | 依存パッケージ |
| `Dockerfile` | コンテナ化（Cloud Run 等） |
| `render.yaml` | Render 用デプロイ設定 |
| `.env.example` | 環境変数の雛形 |

## ローカルで動かす
```bash
cd richmenu_proxy
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # 値を編集
export $(grep -v '^#' .env | xargs)   # .env を読み込む（macOS/Linux）
python app.py               # http://localhost:8000 で起動
```
動作確認: `curl http://localhost:8000/healthz` → `{"ok": true}`

## 本番デプロイ（どちらか）

### A. Render（簡単・無料枠あり）
1. `richmenu_proxy/` の中身を GitHub リポジトリに push
2. render.com → New → Blueprint → そのリポジトリを選択（`render.yaml` を自動検出）
3. 環境変数を設定:
   - `PROXY_KEY` = 長いランダム文字列（合言葉）
   - `ALLOW_ORIGIN` = 管理ページのURL（例 `https://menu.yourcompany.com`）
4. デプロイ完了 → `https://richmenu-proxy-xxxx.onrender.com` が発行される
   → これが**プロキシURL**。管理ページの設定に入力。
   ※無料プランはアクセスが無いとスリープし初回が遅くなります。

### B. Google Cloud Run
```bash
cd richmenu_proxy
gcloud run deploy richmenu-proxy \
  --source . \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --set-env-vars PROXY_KEY="長い合言葉",ALLOW_ORIGIN="https://管理ページURL"
```
発行された `https://richmenu-proxy-xxxx.a.run.app` がプロキシURL。

## 管理ページ側の設定
- 設定画面の「APIプロキシURL」に上記URLを入力。
- 各店舗のチャネルアクセストークンは店舗カードに入力（フロントのブラウザにのみ保存）。
- `PROXY_KEY` を使う場合は、管理ページから `X-Proxy-Key` ヘッダーを送る実装を追加する必要があります
  （現行プロトタイプは未送信。本番アプリ実装時に対応）。

## セキュリティ注意
- `ALLOW_ORIGIN` は必ず管理ページのドメインに限定する（`*` のままにしない）。
- 本番アプリでは、トークンをフロントに置かず **DBに暗号化保存 → バックエンドで付与**する構成を推奨
  （その場合このプロキシはバックエンドに統合し、トークン転送ではなくサーバー保持に変更）。
