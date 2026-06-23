"""
LINE Messaging API 中継プロキシ（本番運用向け）
================================================
ブラウザから api.line.me は CORS で直接呼べないため、この中継サーバーを経由します。

トークンの扱い:
  店舗ごとにトークンが異なるため、管理ページが Authorization ヘッダーで
  トークンを送り、このプロキシはそれをそのまま LINE へ転送します
  （サーバー側にトークンを保存しません）。
  不正利用を防ぐため、X-Proxy-Key（合言葉）での簡易認証を必須にしています。

起動:
  pip install -r requirements.txt
  export PROXY_KEY="任意の長い合言葉"
  export ALLOW_ORIGIN="https://あなたの管理ページのURL"
  gunicorn -b 0.0.0.0:$PORT app:app      # 本番
  python app.py                          # ローカル確認
"""

import os
from flask import Flask, request, Response, abort
from flask_cors import CORS
import requests

# ── 環境変数 ──────────────────────────────────────────────
ALLOW_ORIGIN = os.environ.get("ALLOW_ORIGIN", "*")   # 管理ページのURL。* は本番非推奨
PROXY_KEY    = os.environ.get("PROXY_KEY")            # 合言葉（未設定なら認証なし＝開発用）
TIMEOUT      = int(os.environ.get("TIMEOUT", "30"))

app = Flask(__name__)
CORS(
    app,
    origins=ALLOW_ORIGIN,
    allow_headers=["Content-Type", "Authorization", "X-Proxy-Key"],
    methods=["GET", "POST", "DELETE", "OPTIONS"],
)


@app.route("/healthz")
def healthz():
    return {"ok": True}


@app.route("/<path:p>", methods=["GET", "POST", "DELETE", "OPTIONS"])
def proxy(p):
    if request.method == "OPTIONS":
        return ("", 204)

    # 合言葉チェック（設定されている場合のみ）
    if PROXY_KEY and request.headers.get("X-Proxy-Key") != PROXY_KEY:
        abort(401)

    # 画像アップロード(/content)は api-data、それ以外は api ホスト
    host = "https://api-data.line.me" if "/content" in p else "https://api.line.me"

    headers = {}
    auth = request.headers.get("Authorization")  # 管理ページから来たトークンを転送
    if auth:
        headers["Authorization"] = auth
    ct = request.headers.get("Content-Type")
    if ct:
        headers["Content-Type"] = ct

    try:
        r = requests.request(
            request.method, f"{host}/{p}",
            headers=headers, data=request.get_data(), timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        return Response('{"error":"upstream_error","detail":%r}' % str(e),
                        502, content_type="application/json")

    return Response(r.content, r.status_code,
                    content_type=r.headers.get("Content-Type", "application/json"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
