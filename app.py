"""
LINE Messaging API 中継プロキシ（トークン暗号化保存版）
=========================================================
- LINEのチャネルアクセストークンはこのプロキシだけが保持します。
- トークンは暗号化して保存（保存先 Supabase / 鍵はこのプロキシのみ）。
- 管理サイトは「合言葉(PROXY_KEY)」で認証し、store_id を指定するだけ。
  ブラウザに生のトークンは残りません。

必要な環境変数:
  PROXY_KEY        管理サイトと共有する合言葉（推測されない長い文字列）
  ENCRYPTION_KEY   Fernet鍵（下の生成コマンドで作る）。トークン暗号化に使用
  SUPABASE_URL     例 https://xxxx.supabase.co
  SUPABASE_KEY     Supabaseの service_role キー（サーバー専用・秘匿）
  ALLOW_ORIGIN     管理サイトのURL（CORS）。例 https://richmenu-studio.netlify.app

Fernet鍵の作り方（ローカルで1回）:
  pip install cryptography
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  → 出力を ENCRYPTION_KEY に設定

Supabaseに必要なテーブル（SQL Editorで実行）:
  create table if not exists store_tokens (
    store_id text primary key,
    enc text not null,
    updated_at timestamptz default now()
  );
  alter table store_tokens enable row level security;
  -- service_role はRLSをバイパスするのでポリシー不要（anonからは触れない＝安全）

起動:
  pip install -r requirements.txt
  gunicorn -b 0.0.0.0:$PORT app:app
"""

import os
import requests
from flask import Flask, request, Response, jsonify, abort
from flask_cors import CORS
from cryptography.fernet import Fernet

PROXY_KEY      = os.environ["PROXY_KEY"]
ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"].encode()
SUPABASE_URL   = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
ALLOW_ORIGIN   = os.environ.get("ALLOW_ORIGIN", "*")

fernet = Fernet(ENCRYPTION_KEY)
app = Flask(__name__)
CORS(app, origins=ALLOW_ORIGIN,
     allow_headers=["Content-Type", "X-Proxy-Key", "X-Store-Id"],
     methods=["GET", "POST", "DELETE", "OPTIONS"])

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def require_key():
    if request.headers.get("X-Proxy-Key") != PROXY_KEY:
        abort(401)


def store_put(store_id, token):
    enc = fernet.encrypt(token.encode()).decode()
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/store_tokens",
        headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates"},
        json={"store_id": store_id, "enc": enc},
    )
    r.raise_for_status()


def store_get(store_id):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/store_tokens",
        headers=SB_HEADERS,
        params={"store_id": f"eq.{store_id}", "select": "enc"},
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    return fernet.decrypt(rows[0]["enc"].encode()).decode()


# ── 管理サイト → トークン登録（生のトークンはここで暗号化され、以後は出ない） ──
@app.route("/token", methods=["POST", "OPTIONS"])
def set_token():
    if request.method == "OPTIONS":
        return ("", 204)
    require_key()
    body = request.get_json(force=True)
    sid = (body or {}).get("store_id")
    tok = (body or {}).get("token")
    if not sid or not tok:
        return jsonify(error="store_id and token required"), 400
    store_put(sid, tok)
    return jsonify(ok=True)


# ── 設定状況の確認（トークン自体は返さない） ──
@app.route("/token-status/<store_id>", methods=["GET", "OPTIONS"])
def token_status(store_id):
    if request.method == "OPTIONS":
        return ("", 204)
    require_key()
    try:
        exists = store_get(store_id) is not None
    except Exception:
        exists = False
    return jsonify(set=exists)


@app.route("/healthz")
def healthz():
    return jsonify(ok=True)


# ── LINE API 中継（store_id を指定。トークンはサーバー側で付与） ──
@app.route("/<path:p>", methods=["GET", "POST", "DELETE", "OPTIONS"])
def proxy(p):
    if request.method == "OPTIONS":
        return ("", 204)
    require_key()
    sid = request.headers.get("X-Store-Id")
    if not sid:
        return jsonify(error="X-Store-Id required"), 400
    token = store_get(sid)
    if not token:
        return jsonify(error="token not set for this store"), 400

    host = "https://api-data.line.me" if "/content" in p else "https://api.line.me"
    headers = {"Authorization": f"Bearer {token}"}
    ct = request.headers.get("Content-Type")
    if ct:
        headers["Content-Type"] = ct
    r = requests.request(request.method, f"{host}/{p}",
                         headers=headers, data=request.get_data(), timeout=30)
    return Response(r.content, r.status_code,
                    content_type=r.headers.get("Content-Type", "application/json"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
