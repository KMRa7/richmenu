"""
実行予約ワーカー（完全自動・B方式）
=====================================
schedules テーブルの期限が来た予約を実行します。
- publish: そのメニューを全解除 → 全ページ作成 → 画像アップ → エイリアス → デフォルト設定
- unlink : その店舗の全リッチメニューを解除

Render Cron Job から定期実行します（例: */5 * * * *）。
  Start Command:  python scheduler.py
必要な環境変数（app.py と同じ）:
  ENCRYPTION_KEY / SUPABASE_URL / SUPABASE_KEY
  ※ PROXY_KEY は不要（このワーカーはサーバー内で直接DBとLINEを叩くため）

依存: requests, cryptography  (requirements.txt に既出)
"""

import os
import requests
from cryptography.fernet import Fernet

ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"].encode()
SUPABASE_URL   = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
fernet = Fernet(ENCRYPTION_KEY)

SB = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
API  = "https://api.line.me"
DATA = "https://api-data.line.me"


# ── Supabase ヘルパ ──────────────────────────────────────
def sb_get(table, params):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=SB, params=params)
    r.raise_for_status()
    return r.json()

def sb_patch(table, params, body):
    requests.patch(f"{SUPABASE_URL}/rest/v1/{table}", headers=SB, params=params, json=body)

def get_token(store_id):
    rows = sb_get("store_tokens", {"store_id": f"eq.{store_id}", "select": "enc"})
    if not rows:
        return None
    return fernet.decrypt(rows[0]["enc"].encode()).decode()

def get_menu(menu_id):
    rows = sb_get("menus", {"id": f"eq.{menu_id}", "select": "data"})
    return rows[0]["data"] if rows else None

def get_official_id(store_id):
    rows = sb_get("app_config", {"key": "eq.stores", "select": "value"})
    if not rows:
        return store_id
    import json
    try:
        stores = json.loads(rows[0]["value"])
        for s in stores:
            if s.get("id") == store_id:
                return (s.get("officialId") or store_id)
    except Exception:
        pass
    return store_id


# ── ペイロード構築（管理ページのJSと同じロジック） ──────────
def alias_id(menu, idx):
    return (str(menu["id"]) + "-p" + str(idx + 1)).replace("_", "-").lower()

def scheme_uri(ac, oid):
    sc = ac.get("scheme", "message")
    if sc == "faq":
        return f"https://liff.line.me/1645278921-kWRPP32q/{oid}/faq/{ac.get('faqId','')}?accountId={oid}"
    if sc == "recommend":
        return f"https://line.me/R/nv/recommendOA/%40{oid}"
    from urllib.parse import quote
    return f"https://line.me/R/oaMessage/%40{oid}/?{quote(ac.get('text',''))}"

def build_action(ac, menu, page_id_to_idx, oid):
    t = ac.get("type")
    if t == "uri":
        o = {"type": "uri", "uri": ac.get("uri", "")}
        if ac.get("label"): o["label"] = ac["label"]
        return o
    if t == "scheme":
        o = {"type": "uri", "uri": scheme_uri(ac, oid)}
        if ac.get("label"): o["label"] = ac["label"]
        return o
    if t == "message":
        return {"type": "message", "text": ac.get("text", "")}
    if t == "postback":
        o = {"type": "postback", "data": ac.get("data", "")}
        if ac.get("displayText"): o["displayText"] = ac["displayText"]
        return o
    if t == "richmenuswitch":
        idx = page_id_to_idx.get(ac.get("target"))
        o = {"type": "richmenuswitch",
             "richMenuAliasId": alias_id(menu, idx) if idx is not None else "",
             "data": "switch"}
        if ac.get("label"): o["label"] = ac["label"]
        return o
    return None

def build_payload(menu, page, oid, page_id_to_idx):
    W = 2500
    H = 843 if menu.get("size") == "small" else 1686
    multi = len(menu.get("pages", [])) > 1
    name = menu["name"] + (" - " + page["name"] if multi else "")
    areas = []
    for a in page["areas"]:
        ac = a.get("action") or {}
        if ac.get("type") == "none":
            continue
        if ac.get("type") == "richmenuswitch" and not (ac.get("target") or "").strip():
            continue
        act = build_action(ac, menu, page_id_to_idx, oid)
        if not act:
            continue
        areas.append({
            "bounds": {
                "x": round(a["x"] / 100 * W),
                "y": round(a["y"] / 100 * H),
                "width": round(a["w"] / 100 * W),
                "height": round(a["h"] / 100 * H),
            },
            "action": act,
        })
    return {
        "size": {"width": W, "height": H},
        "selected": menu.get("defaultBehavior") != "collapsed",
        "name": name,
        "chatBarText": menu.get("chatBarText", "メニュー"),
        "areas": areas,
    }


# ── LINE 操作 ───────────────────────────────────────────
def line_headers(token, content_type="application/json"):
    return {"Authorization": f"Bearer {token}", "Content-Type": content_type}

def unset_default(token):
    requests.delete(f"{API}/v2/bot/user/all/richmenu", headers=line_headers(token))

def list_menus(token):
    r = requests.get(f"{API}/v2/bot/richmenu/list", headers=line_headers(token))
    return r.json().get("richmenus", []) if r.ok else []

def delete_menu(token, rid):
    requests.delete(f"{API}/v2/bot/richmenu/{rid}", headers=line_headers(token))

def create_menu(token, body):
    r = requests.post(f"{API}/v2/bot/richmenu", headers=line_headers(token), json=body)
    r.raise_for_status()
    return r.json()["richMenuId"]

def upload_image(token, rid, image_url):
    img = requests.get(image_url)
    ct = img.headers.get("Content-Type", "image/png")
    requests.post(f"{DATA}/v2/bot/richmenu/{rid}/content",
                  headers={"Authorization": f"Bearer {token}", "Content-Type": ct},
                  data=img.content)

def set_default(token, rid):
    requests.post(f"{API}/v2/bot/user/all/richmenu/{rid}", headers=line_headers(token))

def create_alias(token, alias_id_, rid):
    requests.delete(f"{API}/v2/bot/richmenu/alias/{alias_id_}", headers=line_headers(token))
    requests.post(f"{API}/v2/bot/richmenu/alias", headers=line_headers(token),
                  json={"richMenuAliasId": alias_id_, "richMenuId": rid})


# ── 予約の実行 ───────────────────────────────────────────
def do_unlink(token):
    unset_default(token)
    for rm in list_menus(token):
        delete_menu(token, rm["richMenuId"])

def do_publish(token, menu, oid):
    # 1) 既存を全解除
    do_unlink(token)
    pages = menu.get("pages", [])
    page_id_to_idx = {p["id"]: i for i, p in enumerate(pages)}
    created = []
    # 2) 各ページを作成＋画像
    for p in pages:
        body = build_payload(menu, p, oid, page_id_to_idx)
        rid = create_menu(token, body)
        if p.get("image"):
            upload_image(token, rid, p["image"])
        created.append((p["id"], rid))
    # 3) エイリアス（複数ページのみ）
    if len(pages) > 1:
        for i, (pid, rid) in enumerate(created):
            create_alias(token, alias_id(menu, i), rid)
    # 4) デフォルト設定
    if created and menu.get("isDefault"):
        set_default(token, created[0][1])


def run_due():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    due = sb_get("schedules", {"status": "eq.pending", "run_at": f"lte.{now}",
                               "select": "*", "order": "run_at.asc"})
    print(f"due schedules: {len(due)}")
    for sc in due:
        sid = sc["store_id"]
        # 排他: pending → running
        sb_patch("schedules", {"id": f"eq.{sc['id']}", "status": "eq.pending"},
                 {"status": "running"})
        try:
            token = get_token(sid)
            if not token:
                raise RuntimeError("token not set")
            oid = get_official_id(sid)
            if sc["kind"] == "publish":
                menu = get_menu(sc["menu_id"])
                if not menu:
                    raise RuntimeError("menu not found")
                do_publish(token, menu, oid)
            else:
                do_unlink(token)
            sb_patch("schedules", {"id": f"eq.{sc['id']}"}, {"status": "done"})
            print(f"  ✓ {sc['kind']} {sc.get('menu_name','')}")
        except Exception as e:
            sb_patch("schedules", {"id": f"eq.{sc['id']}"}, {"status": "error"})
            print(f"  ✗ {sc['id']}: {e}")


if __name__ == "__main__":
    run_due()
