#!/usr/bin/env python3
"""Subscription bot + Telegram Mini App (no payments).
Roles: admin (ADMIN_IDS) and allowed users (users.json). Allowed users can create
their own subscription once; admin can add/remove users, create/disable clients."""
import hashlib, hmac, json, os, re, secrets, smtplib, socket, threading, time, urllib.request, urllib.parse
from urllib.parse import quote as _q
from email.message import EmailMessage
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ["BOT_TOKEN"]
PANEL = os.environ["PANEL_API"].rstrip("/")
PTOKEN = os.environ["PANEL_TOKEN"]
SUB_BASE = os.environ["SUB_BASE"].rstrip("/")
ADMINS = {x.strip() for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
INBOUNDS = [int(x) for x in os.environ.get("INBOUND_IDS", "1").split(",") if x.strip()]
APP_URL = os.environ.get("APP_URL", "")
SUBS_LIMIT = int(os.environ.get("SUBS_LIMIT", "10"))
SUPPORT = os.environ.get("SUPPORT", "@telenokpetr")
PROXY_HOST = os.environ.get("PROXY_HOST", "194.39.110.184")
PROXY_LIMIT = int(os.environ.get("PROXY_LIMIT", "2"))
PROXY_PORTS = range(int(os.environ.get("PROXY_PORT_FROM", "28081")), int(os.environ.get("PROXY_PORT_TO", "28280")) + 1)
TON_ADDRESS = os.environ.get("TON_ADDRESS", "").strip()
OS_LIST = ["iOS", "Android", "Windows", "macOS", "Linux", "Роутер"]
BOT_USERNAME = os.environ.get("BOT_USERNAME", "DeadObxoD_bot")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or SMTP_USER
SESS_FILE = "/opt/sub-bot/sessions.json"
SESS_DAYS = 30
WEBTOK = {}       # Telegram login token -> {"uid": None | str, "exp": ts}
EMAIL_CODES = {}  # email -> {"hash": str, "exp": ts, "tries": int, "sent": ts}
SUB_DAYS = int(os.environ.get("SUB_DAYS", "30"))
PROTECTED = {x.strip().lower() for x in os.environ.get(
    "PROTECTED_EMAILS", "phone,PCClaudeCode,RouterRR,Pixel8my,Pyxel,RR,Telenokpetr,AiClaude").split(",") if x.strip()}
USERS_FILE = "/opt/sub-bot/users.json"
API = f"https://api.telegram.org/bot{TOKEN}"
LOCK = threading.Lock()


def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{API}/{method}", data=data), timeout=40) as r:
        return json.load(r)


def panel(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(PANEL + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Authorization": f"Bearer {PTOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def load_users():
    try:
        return json.load(open(USERS_FILE))
    except Exception:
        return {}


def save_users(u):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(u, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, USERS_FILE)


def user_emails(entry):
    """All client emails belonging to a user (supports the old single-email format)."""
    emails = list(entry.get("emails") or [])
    if entry.get("email") and entry["email"] not in emails:
        emails.insert(0, entry["email"])
    return emails


def clients():
    return panel("/clients/list")["obj"]


def find_client(email):
    for c in clients():
        if c["email"].lower() == email.lower():
            return c


def protected(email):
    """Never expire or delete: the admin's own and router clients."""
    e = email.lower()
    return (e in PROTECTED or "router" in e or any(e == "tg" + a or e.startswith("tg" + a + "-") for a in ADMINS))


def panel_update(email, **fields):
    r = panel("/clients/update/" + _q(email, safe=""), dict(fields, email=fields.get("email", email)) if "email" in fields
              else dict(fields, email=email))
    if not r.get("success"):
        raise ValueError("Панель отказала: " + str(r.get("msg"))[:120])


def panel_delete(email):
    r = panel("/clients/del/" + _q(email, safe=""), {})
    return bool(r.get("success"))


def client_base_name(uid, username):
    """Subscription name: Telegram nick if there is one, otherwise tg<number>."""
    nick = re.sub(r"[^A-Za-z0-9_.-]", "", username or "")
    return nick if len(nick) >= 3 else "tg" + uid


def unique_name(base, taken=()):
    name, n = base, 1
    while name.lower() in {t.lower() for t in taken} or find_client(name):
        n += 1
        name = f"{base}-{n}"
    return name


def sub_link(c):
    return f"{SUB_BASE}/{c['subId']}"


def fmt(b):
    for u in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if b < 1024 or u == "ТБ":
            return f"{b:.1f} {u}"
        b /= 1024


def client_info(c):
    t = c.get("traffic", {})
    exp = c.get("expiryTime", 0)
    days_left = None
    if exp and exp > 0:
        days_left = int((exp / 1000 - time.time()) // 86400)
    return {"days_left": days_left, "email": c["email"], "enable": c["enable"], "used": fmt(t.get("up", 0) + t.get("down", 0)),
            "limit": fmt(c["totalGB"]) if c.get("totalGB") else "без лимита",
            "expire": time.strftime("%d.%m.%Y", time.localtime(exp / 1000)) if exp > 0 else "без срока",
            "link": sub_link(c)}


def create_client(email, gb=0, days=SUB_DAYS, comment=""):
    if find_client(email):
        raise ValueError("Клиент с таким именем уже существует")
    body = {"client": {"email": email, "enable": True, "flow": "", "totalGB": int(gb * 1024 ** 3),
                       "expiryTime": int((time.time() + days * 86400) * 1000) if days else 0,
                       "limitIp": 0, "comment": comment}, "inboundIds": INBOUNDS}
    r = panel("/clients/add", body)
    if not r.get("success"):
        raise ValueError("Панель отказала: " + str(r.get("msg"))[:120])
    time.sleep(2)
    return find_client(email)


def check_init_data(init):
    """Validate Telegram WebApp initData (HMAC) and return the user dict."""
    try:
        pairs = dict(urllib.parse.parse_qsl(init, keep_blank_values=True))
        got = pairs.pop("hash")
        s = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(key, s.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > 86400:
            return None
        return json.loads(pairs["user"])
    except Exception:
        return None



def all_inbounds():
    return panel("/inbounds/list")["obj"]


def _settings(ib):
    s = ib.get("settings") or {}
    return json.loads(s) if isinstance(s, str) else s


def proxy_info(ib):
    acc = (_settings(ib).get("accounts") or [{}])[0]
    user, pw = acc.get("user", ""), acc.get("pass", "")
    port = ib["port"]
    return {"port": port, "name": str(ib.get("remark", "")).replace("TG SOCKS5", "", 1).strip() or str(port),
            "enable": bool(ib.get("enable")), "host": PROXY_HOST, "user": user, "pass": pw,
            "link": f"https://t.me/socks?server={PROXY_HOST}&port={port}&user={user}&pass={pw}"}


def proxy_inbounds():
    return [ib for ib in all_inbounds() if str(ib.get("remark", "")).startswith("TG SOCKS5")]


def create_proxy(label=""):
    label = "".join(ch for ch in str(label) if ch.isalnum() or ch in "-_")[:20]
    used = {ib["port"] for ib in all_inbounds()}
    port = next((p for p in PROXY_PORTS if p not in used), None)
    if port is None:
        raise ValueError("Свободные порты для прокси закончились")
    user, pw = "tg" + os.urandom(3).hex(), os.urandom(10).hex()
    body = {"enable": True, "remark": ("TG SOCKS5 " + label).strip(), "listen": "", "port": port,
            "protocol": "mixed", "expiryTime": 0, "total": 0,
            "settings": {"auth": "password", "accounts": [{"user": user, "pass": pw}], "udp": True, "ip": PROXY_HOST},
            "streamSettings": {"network": "tcp", "security": "none"},
            "sniffing": {"enabled": False, "destOverride": []}}
    r = panel("/inbounds/add", body)
    if not r.get("success"):
        raise ValueError("Панель отказала: " + str(r.get("msg"))[:100])
    return {"port": port, "name": label or str(port), "enable": True, "host": PROXY_HOST, "user": user, "pass": pw,
            "link": f"https://t.me/socks?server={PROXY_HOST}&port={port}&user={user}&pass={pw}"}


def set_proxy_enabled(port, enable):
    for ib in proxy_inbounds():
        if ib["port"] == port:
            full = panel("/inbounds/get/%d" % ib["id"])["obj"]
            full["enable"] = bool(enable)
            for k in ("up", "down", "clientStats"):
                full.pop(k, None)
            return bool(panel("/inbounds/update/%d" % ib["id"], full).get("success"))
    raise ValueError("Прокси не найден")


# ---- web cabinet: sessions, Telegram and e-mail login ----
def _load_sessions():
    try:
        return json.load(open(SESS_FILE))
    except Exception:
        return {}


def _save_sessions(d):
    tmp = SESS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SESS_FILE)


def new_session(uid):
    sid = secrets.token_urlsafe(32)
    with LOCK:
        d = {k: v for k, v in _load_sessions().items() if v.get("exp", 0) > time.time()}
        d[hashlib.sha256(sid.encode()).hexdigest()] = {"uid": uid, "exp": time.time() + SESS_DAYS * 86400}
        _save_sessions(d)
    return sid


def session_uid(cookie_header):
    try:
        sid = SimpleCookie(cookie_header or "")["sid"].value
    except Exception:
        return None
    v = _load_sessions().get(hashlib.sha256(sid.encode()).hexdigest())
    return v["uid"] if v and v.get("exp", 0) > time.time() else None


def drop_session(cookie_header):
    try:
        sid = SimpleCookie(cookie_header or "")["sid"].value
    except Exception:
        return
    with LOCK:
        d = _load_sessions()
        d.pop(hashlib.sha256(sid.encode()).hexdigest(), None)
        _save_sessions(d)


def uid_by_email(email):
    for k, v in load_users().items():
        if (v.get("login_email") or "").lower() == email:
            return k


def send_mail(to, subject, body):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP_FROM, to, subject
    msg.set_content(body)
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as sm:
            sm.login(SMTP_USER, SMTP_PASS)
            sm.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as sm:
            sm.starttls()
            sm.login(SMTP_USER, SMTP_PASS)
            sm.send_message(msg)


def web_login_api(req):
    """Returns (response dict, new session id or None, logout flag)."""
    act = req.get("action")
    now = time.time()
    if act == "web_tg_start":
        for k in [k for k, v in WEBTOK.items() if v["exp"] < now]:
            WEBTOK.pop(k, None)
        tok = secrets.token_urlsafe(12)
        WEBTOK[tok] = {"uid": None, "exp": now + 600}
        return {"token": tok, "link": f"https://t.me/{BOT_USERNAME}?start=web_{tok}"}, None, False
    if act == "web_tg_poll":
        ent = WEBTOK.get(str(req.get("token", "")))
        if not ent or ent["exp"] < now:
            return {"error": "Ссылка устарела, начните вход заново"}, None, False
        if not ent["uid"]:
            return {"pending": True}, None, False
        WEBTOK.pop(str(req.get("token")), None)
        return {"ok": True}, new_session(ent["uid"]), False
    if act == "web_email_request":
        if not SMTP_HOST:
            return {"error": "Вход по почте пока не настроен. Войдите через Telegram."}, None, False
        em = str(req.get("email", "")).strip().lower()
        uid = uid_by_email(em) if em else None
        cur = EMAIL_CODES.get(em)
        if uid and not (cur and now - cur["sent"] < 60):
            code = f"{secrets.randbelow(10 ** 6):06d}"
            EMAIL_CODES[em] = {"hash": hashlib.sha256(code.encode()).hexdigest(), "exp": now + 600, "tries": 0, "sent": now}
            threading.Thread(target=lambda: _mail_code(em, code), daemon=True).start()
        return {"ok": True}, None, False  # same answer whether the address exists or not
    if act == "web_email_verify":
        em = str(req.get("email", "")).strip().lower()
        ent = EMAIL_CODES.get(em)
        if not ent or ent["exp"] < now or ent["tries"] >= 5:
            return {"error": "Код недействителен, запросите новый"}, None, False
        ent["tries"] += 1
        code = str(req.get("code", "")).strip()
        if not hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), ent["hash"]):
            return {"error": "Неверный код"}, None, False
        EMAIL_CODES.pop(em, None)
        uid = uid_by_email(em)
        if not uid:
            return {"error": "Код недействителен, запросите новый"}, None, False
        return {"ok": True}, new_session(uid), False
    if act == "web_logout":
        return {"ok": True}, None, True
    return None, None, False


def _mail_code(em, code):
    try:
        send_mail(em, f"Код входа: {code}", f"Ваш код для входа в кабинет Деда Обхода: {code}\n\nОн действует 10 минут. Если это не вы, просто проигнорируйте письмо.")
    except Exception as e:
        print("mail error:", type(e).__name__, str(e)[:80], flush=True)


def sync_username(uid, username):
    """Store the nick and rename this user's old tg<id>-style subscriptions to it."""
    with LOCK:
        users = load_users()
        entry = users.get(uid)
        if entry is None:
            return
        changed = entry.get("username") != username
        entry["username"] = username
        base = client_base_name(uid, username)
        emails = user_emails(entry)
        for i, e in enumerate(emails):
            if base != "tg" + uid and re.fullmatch(r"tg%s(-\d+)?" % uid, e):
                new = unique_name(base, emails)
                panel_update(e, email=new)
                emails[i] = new
                changed = True
        if changed:
            entry["emails"] = emails
            entry.pop("email", None)
            users[uid] = entry
            save_users(users)


def api(user, req):
    uid = str(user["id"])
    admin = uid in ADMINS
    users = load_users()
    allowed = admin or uid in users
    act = req.get("action")
    if allowed and user.get("username") and act == "me":
        try:
            sync_username(uid, user["username"])
        except Exception as e:
            print("sync_username error:", type(e).__name__, str(e)[:80], flush=True)
    if act == "me":
        info = {"id": uid, "name": user.get("first_name", ""), "admin": admin, "allowed": allowed,
                "support": SUPPORT, "down": [n for n, s in STATUS.items() if not s["up"]]}
        info["ton"] = TON_ADDRESS
        info["login_email"] = users.get(uid, {}).get("login_email")
        info["email_login"] = bool(SMTP_HOST)
        info["profile"] = users.get(uid, {}).get("profile")
        info["os_list"] = OS_LIST
        if allowed:
            found = [find_client(e) for e in user_emails(users.get(uid, {}))]
            info["clients"] = [client_info(c) for c in found if c]
            info["limit"] = None if admin else SUBS_LIMIT
            info["proxy_limit"] = None if admin else PROXY_LIMIT
        return info
    if not allowed:
        return {"error": "Нет доступа. Передайте администратору ваш ID: " + uid}
    if act == "renew":
        email = str(req.get("email", ""))
        if not admin and email not in user_emails(users.get(uid, {})):
            return {"error": "Это не ваша подписка"}
        c = find_client(email)
        if not c:
            return {"error": "Подписка не найдена"}
        exp = c.get("expiryTime", 0)
        if not exp:
            return {"error": "У этой подписки нет срока: продлевать не нужно"}
        if exp / 1000 - time.time() > 10 * 86400 and not admin:
            return {"error": "Продлить можно за 10 дней до окончания"}
        base = max(exp, time.time() * 1000)
        panel_update(email, expiryTime=int(base + SUB_DAYS * 86400 * 1000))
        return {"ok": True}
    if act == "email_set":
        em = str(req.get("email", "")).strip().lower()
        if em and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", em):
            return {"error": "Некорректный адрес почты"}
        with LOCK:
            users = load_users()
            if em and any(k != uid and (v.get("login_email") or "").lower() == em for k, v in users.items()):
                return {"error": "Эта почта уже привязана к другому аккаунту"}
            entry = users.get(uid, {})
            if em:
                entry["login_email"] = em
            else:
                entry.pop("login_email", None)
            users[uid] = entry
            save_users(users)
        return {"ok": True}
    if act == "profile_set":
        name = str(req.get("name", "")).strip()[:40]
        os_ = str(req.get("os", ""))
        if not name:
            return {"error": "Укажите имя"}
        if os_ not in OS_LIST:
            return {"error": "Выберите операционную систему"}
        with LOCK:
            users = load_users()
            entry = users.get(uid, {})
            entry["profile"] = {"name": name, "os": os_}
            entry.setdefault("note", name)
            users[uid] = entry
            save_users(users)
        return {"ok": True}
    if act == "donate_link":
        try:
            amount = int(req.get("amount"))
        except (TypeError, ValueError):
            return {"error": "Укажите количество звёзд"}
        if not 1 <= amount <= 100000:
            return {"error": "От 1 до 100000 звёзд"}
        r = tg("createInvoiceLink", title="Поддержать деда",
               description="Добровольный донат на развитие проекта. Спасибо!",
               payload="donate", provider_token="", currency="XTR",
               prices=json.dumps([{"label": "Донат", "amount": amount}]))
        if not r.get("ok"):
            return {"error": "Не удалось создать счёт"}
        return {"link": r["result"]}
    if act == "create_self":
        with LOCK:
            users = load_users()
            entry = users.get(uid, {})
            emails = user_emails(entry)
            if not admin and len(emails) >= SUBS_LIMIT:
                return {"error": f"Достигнут лимит: {SUBS_LIMIT} подписок"}
            if user.get("username"):
                entry["username"] = user["username"]
            email = unique_name(client_base_name(uid, entry.get("username")), emails)
            c = create_client(email, comment="self-service tg " + uid)
            entry["emails"] = emails + [email]
            entry.pop("email", None)
            users[uid] = entry
            save_users(users)
        return {"client": client_info(c)}
    if act == "proxies":
        mine = set(users.get(uid, {}).get("proxies", []))
        return {"proxies": [proxy_info(ib) for ib in proxy_inbounds() if admin or ib["port"] in mine]}
    if act == "proxy_create":
        with LOCK:
            users = load_users()
            entry = users.get(uid, {})
            owned = entry.get("proxies", [])
            if not admin and len(owned) >= PROXY_LIMIT:
                return {"error": f"Достигнут лимит: {PROXY_LIMIT} прокси"}
            p = create_proxy(req.get("label", ""))
            entry["proxies"] = owned + [p["port"]]
            users[uid] = entry
            save_users(users)
        return {"proxy": p}
    if not admin:
        return {"error": "Только для администратора"}
    if act == "list_users":
        return {"users": [dict(id=k, **v) for k, v in users.items()]}
    if act == "add_user":
        tid = str(req.get("id", "")).strip()
        if not tid.isdigit():
            return {"error": "ID должен быть числом"}
        with LOCK:
            users = load_users()
            cur = users.get(tid, {"email": None})
            cur["note"] = str(req.get("note", cur.get("note", "")))[:60]
            if req.get("os") in OS_LIST and cur["note"]:
                cur["profile"] = {"name": cur["note"][:40], "os": req["os"]}
            users[tid] = cur
            save_users(users)
        return {"ok": True}
    if act == "remove_user":
        with LOCK:
            users = load_users()
            users.pop(str(req.get("id")), None)
            save_users(users)
        return {"ok": True}
    if act == "list_clients":
        return {"clients": [client_info(c) for c in clients()]}
    if act == "create_client":
        name = str(req.get("name", "")).strip()
        if not name or len(name) > 40:
            return {"error": "Укажите имя (до 40 символов)"}
        dv = req.get("days")
        c = create_client(name, float(req.get("gb") or 0), SUB_DAYS if dv in (None, "") else int(dv), "created by admin")
        return {"client": client_info(c)}
    if act in ("disable_client", "enable_client"):
        r = panel("/clients/" + ("bulkDisable" if act == "disable_client" else "bulkEnable"),
                  {"emails": [str(req.get("email"))]})
        return {"ok": bool(r.get("success"))}
    if act in ("proxy_disable", "proxy_enable"):
        return {"ok": set_proxy_enabled(int(req.get("port")), act == "proxy_enable")}
    return {"error": "unknown action"}


# ---- health monitoring: notify users when a channel is down ----
CHECKS = [
    ("Обход блокировок 1", "https", "https://media.scout-service.com/"),
    ("Обход блокировок 2", "https", "https://img.scout-service.com/"),
    ("🇳🇱 Нидерланды", "tcp", ("media-sync.scout-service.com", 443)),
    ("🇳🇱 Нидерланды 2", "tcp", ("pkg-store.scout-service.com", 37954)),
]
FAILS_TO_ALERT = 3
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "1800"))
STATUS = {name: {"up": True, "fails": 0, "since": time.time()} for name, _, _ in CHECKS}


def probe(kind, target):
    try:
        if kind == "https":
            req = urllib.request.Request(target, headers={"User-Agent": "status-check"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status == 200
        with socket.create_connection(target, timeout=8):
            return True
    except Exception:
        return False


def recipients():
    ids = set(ADMINS) | set(load_users().keys())
    return sorted(i for i in ids if i.isdigit())


def broadcast(text):
    for rid in recipients():
        try:
            tg("sendMessage", chat_id=rid, text=text)
        except Exception as e:
            print("notify failed:", type(e).__name__, flush=True)
        time.sleep(0.05)


def status_text():
    lines = [("✅ " if s["up"] else "⚠️ ") + name for name, s in STATUS.items()]
    return "\n".join(lines)


def confirmed_down(kind, target):
    """A channel counts as down only if it fails several quick re-checks in a row."""
    for i in range(FAILS_TO_ALERT):
        if probe(kind, target):
            return False
        if i < FAILS_TO_ALERT - 1:
            time.sleep(20)
    return True


def monitor_loop():
    time.sleep(30)
    while True:
        for name, kind, target in CHECKS:
            s = STATUS[name]
            if s["up"]:
                if confirmed_down(kind, target):
                    s["up"], s["since"] = False, time.time()
                    note_down(name)
                    others = [n for n, v in STATUS.items() if v["up"]]
                    hint = ("\nПока можно переключиться на: " + ", ".join(others)) if others else ""
                    broadcast(f"⚠️ Сервер «{name}» недоступен.{hint}\nЕсли проблема не уходит, напишите {SUPPORT}")
            elif probe(kind, target):
                note_up(name, s["since"])
                s["up"], s["since"] = True, time.time()
                broadcast(f"✅ «{name}» снова работает. Если соединение не восстановилось само, обновите подписку в приложении.")
        try:
            risk_check()
        except Exception as e:
            print("risk error:", type(e).__name__, flush=True)
        time.sleep(CHECK_INTERVAL)


# ---- period statistics and twice-a-day report ----
STATS_FILE = "/opt/sub-bot/stats.json"
REPORT_HOURS = [int(x) for x in os.environ.get("REPORT_HOURS", "9,21").split(",") if x.strip()]
TZ_OFFSET = int(os.environ.get("REPORT_TZ_OFFSET", "3"))  # Moscow, no DST
PERIOD = {"start": time.time(), "incidents": {}, "downtime": {}}


def load_period():
    try:
        d = json.load(open(STATS_FILE))
        if isinstance(d, dict) and "start" in d:
            PERIOD.update(d)
    except Exception:
        pass


def save_period():
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(PERIOD, f)
        os.chmod(STATS_FILE, 0o600)
    except Exception:
        pass


MONTH_FILE = "/opt/sub-bot/month.json"
MONTH = {"start": time.time(), "incidents": {}, "downtime": {}, "sent": ""}


def load_month():
    try:
        d = json.load(open(MONTH_FILE))
        if isinstance(d, dict) and "start" in d:
            MONTH.update(d)
    except Exception:
        pass


def save_month():
    try:
        with open(MONTH_FILE, "w") as f:
            json.dump(MONTH, f)
        os.chmod(MONTH_FILE, 0o600)
    except Exception:
        pass


def note_down(name):
    PERIOD["incidents"][name] = PERIOD["incidents"].get(name, 0) + 1
    MONTH["incidents"][name] = MONTH["incidents"].get(name, 0) + 1
    save_period()
    save_month()


def note_up(name, since):
    d = max(0, time.time() - since)
    PERIOD["downtime"][name] = PERIOD["downtime"].get(name, 0) + d
    MONTH["downtime"][name] = MONTH["downtime"].get(name, 0) + d
    save_period()
    save_month()


def local_load():
    try:
        la = open("/proc/loadavg").read().split()[0]
        mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        ram = 100 - 100 * mem["MemAvailable"] / mem["MemTotal"]
        st = os.statvfs("/")
        disk = 100 - 100 * st.f_bavail / st.f_blocks
        return f"нагрузка {la}, RAM {ram:.0f}%, диск {disk:.0f}%"
    except Exception:
        return "нет данных"


def build_report():
    now = time.time()
    hours = max(1, round((now - PERIOD["start"]) / 3600))
    lines = [f"📊 Отчёт за последние ~{hours} ч", "", "Нагрузка на серверы:", f"• Главный (Хельсинки): {local_load()}"]
    try:
        for n in panel("/nodes/list")["obj"]:
            state = "онлайн" if n.get("status") == "online" else "НЕДОСТУПЕН"
            if n.get("status") == "online":
                lines.append(f"• {n['name']}: CPU {n.get('cpuPct', 0):.0f}%, RAM {n.get('memPct', 0):.0f}%, {state}, клиентов {n.get('clientCount', '?')}")
            else:
                lines.append(f"• {n['name']}: {state}")
    except Exception:
        lines.append("• ноды: не удалось получить данные")
    try:
        online = panel("/clients/onlines", {})["obj"] or []
        lines.append(f"Сейчас онлайн: {len(online)}")
    except Exception:
        pass
    lines += ["", "Сбои за период:"]
    total, down_s = 0, 0
    for name, _, _ in CHECKS:
        inc = PERIOD["incidents"].get(name, 0)
        dt = PERIOD["downtime"].get(name, 0)
        if not STATUS[name]["up"]:
            dt += now - STATUS[name]["since"]
        total += inc
        down_s += dt
        extra = f", простой {dt / 60:.0f} мин" if dt else ""
        lines.append(f"• {name}: {inc}{extra}")
    lines.append(f"Всего сбоев: {total}, суммарный простой: {down_s / 60:.0f} мин")
    lines.append("Сейчас: " + ("все каналы работают ✅" if all(s["up"] for s in STATUS.values()) else "есть недоступные ⚠️"))
    return "\n".join(lines)


def send_report():
    text = build_report()
    for aid in sorted(a for a in ADMINS if a.isdigit()):
        try:
            tg("sendMessage", chat_id=aid, text=text)
        except Exception as e:
            print("report failed:", type(e).__name__, flush=True)
    PERIOD.update({"start": time.time(), "incidents": {}, "downtime": {}})
    save_period()


def report_loop():
    load_period()
    done = None
    while True:
        t = time.gmtime(time.time() + TZ_OFFSET * 3600)
        key = (t.tm_yday, t.tm_hour)
        if t.tm_hour in REPORT_HOURS and key != done:
            done = key
            send_report()
        time.sleep(60)


# ---- standard messages: monthly summary, expiry reminders, risk warnings ----
RISK_COOLDOWN = 12 * 3600
_last_risk = [0.0]


def risk_findings():
    """Things that may cause an outage soon (not an outage yet)."""
    out = []
    try:
        la = float(open("/proc/loadavg").read().split()[0])
        mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        ram = 100 - 100 * mem["MemAvailable"] / mem["MemTotal"]
        st = os.statvfs("/")
        disk = 100 - 100 * st.f_bavail / st.f_blocks
        if ram > 90:
            out.append(f"главный сервер: память {ram:.0f}%")
        if disk > 90:
            out.append(f"главный сервер: диск {disk:.0f}%")
        if la > (os.cpu_count() or 1) * 2:
            out.append(f"главный сервер: высокая нагрузка {la:.1f}")
    except Exception:
        pass
    try:
        for n in panel("/nodes/list")["obj"]:
            if n.get("status") != "online":
                out.append(f"{n['name']}: недоступен")
            elif n.get("cpuPct", 0) > 90 or n.get("memPct", 0) > 90:
                out.append(f"{n['name']}: перегрузка (CPU {n.get('cpuPct', 0):.0f}%, RAM {n.get('memPct', 0):.0f}%)")
    except Exception:
        pass
    for name, cnt in PERIOD["incidents"].items():
        if cnt >= 3:
            out.append(f"«{name}»: {cnt} сбоев за последние сутки")
    return out


def risk_check():
    if time.time() - _last_risk[0] < RISK_COOLDOWN:
        return
    found = risk_findings()
    if found:
        _last_risk[0] = time.time()
        broadcast("⚠️ Возможны перебои в работе.\n• " + "\n• ".join(found) +
                  f"\nЕсли соединение пропадёт, обновите подписку и смените профиль. Помощь: {SUPPORT}")


def user_buttons(uid):
    rows = []
    users = load_users()
    for e in user_emails(users.get(uid, {})):
        c = find_client(e)
        if c and c.get("enable"):
            rows.append([{"text": "🔗 Ссылка для подключения", "url": sub_link(c)}])
            break
    if APP_URL:
        rows.append([{"text": "Открыть приложение", "web_app": {"url": APP_URL}}])
    return {"reply_markup": json.dumps({"inline_keyboard": rows})} if rows else {}


def sub_lines(uid):
    lines = []
    users = load_users()
    for e in user_emails(users.get(uid, {})):
        c = find_client(e)
        if not c:
            continue
        exp = c.get("expiryTime", 0)
        if exp and exp > 0:
            left = int((exp / 1000 - time.time()) // 86400)
            when = time.strftime("%d.%m.%Y", time.localtime(exp / 1000))
            lines.append(f"• {e}: до {when}" + (f" (осталось {left} дн.)" if left >= 0 else " (срок истёк)"))
        else:
            lines.append(f"• {e}: бессрочная")
    return lines


def build_digest():
    now = time.time()
    days = max(1, round((now - MONTH["start"]) / 86400))
    lines = [f"📅 Сводка от Деда за {days} дн.", "", "Стабильность серверов:"]
    for name, _, _ in CHECKS:
        inc = MONTH["incidents"].get(name, 0)
        dt = MONTH["downtime"].get(name, 0)
        if not STATUS[name]["up"]:
            dt += now - STATUS[name]["since"]
        up = max(0.0, 100 - 100 * dt / max(1, now - MONTH["start"]))
        lines.append(f"• {name}: {up:.2f}% работы, сбоев {inc}" + (f", простой {dt / 60:.0f} мин" if dt else ""))
    lines.append("Сейчас: " + ("все каналы работают ✅" if all(s["up"] for s in STATUS.values()) else "есть недоступные ⚠️"))
    risks = risk_findings()
    if risks:
        lines += ["", "⚠️ Возможны перебои:"] + ["• " + r for r in risks]
    return "\n".join(lines)


def send_digest():
    base = build_digest()
    for rid in recipients():
        mine = sub_lines(rid)
        text = base + ("\n\nВаши подписки:\n" + "\n".join(mine) +
                       "\nПодписка действует месяц. Продлить можно в приложении за 10 дней до окончания." if mine else "")
        try:
            tg("sendMessage", chat_id=rid, text=text, **user_buttons(rid))
        except Exception as e:
            print("digest failed:", type(e).__name__, flush=True)
        time.sleep(0.05)
    MONTH.update({"start": time.time(), "incidents": {}, "downtime": {}})
    save_month()


def remind_expiring():
    users = load_users()
    changed = False
    for uid, entry in users.items():
        if not uid.isdigit():
            continue
        sent = entry.setdefault("reminded", {})
        for e in user_emails(entry):
            c = find_client(e)
            exp = (c or {}).get("expiryTime", 0)
            if not c or not exp:
                continue
            left = exp / 1000 - time.time()
            if 0 < left <= 3 * 86400 and sent.get(e) != exp:
                try:
                    tg("sendMessage", chat_id=uid, text=f"⏳ Подписка «{e}» заканчивается через {max(1, int(left // 86400) + 1)} дн. "
                       "Продлите её в приложении (бесплатно, на месяц), иначе она будет удалена.", **user_buttons(uid))
                    sent[e] = exp
                    changed = True
                except Exception as ex:
                    print("remind failed:", type(ex).__name__, flush=True)
    if changed:
        with LOCK:
            fresh = load_users()
            for uid, entry in users.items():
                if uid in fresh:
                    fresh[uid]["reminded"] = entry.get("reminded", {})
            save_users(fresh)


def monthly_loop():
    load_month()
    done_day = None
    while True:
        t = time.gmtime(time.time() + TZ_OFFSET * 3600)
        month_key = f"{t.tm_year}-{t.tm_mon:02d}"
        try:
            if t.tm_mday == 1 and t.tm_hour >= 12 and MONTH.get("sent") != month_key:
                MONTH["sent"] = month_key
                send_digest()
                save_month()
            if t.tm_hour == 12 and done_day != t.tm_yday:
                done_day = t.tm_yday
                remind_expiring()
        except Exception as e:
            print("monthly error:", type(e).__name__, str(e)[:100], flush=True)
        time.sleep(300)


# ---- monthly subscriptions: delete expired ones ----
def cleanup_expired():
    now = time.time() * 1000
    removed = []
    for c in clients():
        exp = c.get("expiryTime", 0)
        if exp and exp < now and not protected(c["email"]):
            if panel_delete(c["email"]):
                removed.append(c["email"])
    if not removed:
        return
    with LOCK:
        users = load_users()
        for uid, entry in users.items():
            mine = [e for e in user_emails(entry) if e in removed]
            if not mine:
                continue
            entry["emails"] = [e for e in user_emails(entry) if e not in removed]
            entry.pop("email", None)
            try:
                tg("sendMessage", chat_id=uid, text="Срок подписки " + ", ".join(mine) +
                   " истёк, она удалена. Создайте новую в приложении.")
            except Exception:
                pass
        save_users(users)
    print("expired subscriptions removed:", removed, flush=True)


def cleanup_loop():
    while True:
        try:
            cleanup_expired()
        except Exception as e:
            print("cleanup error:", type(e).__name__, str(e)[:100], flush=True)
        time.sleep(600)


HTML = open("/opt/sub-bot/index.html", encoding="utf-8").read()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", headers=()):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/web"):
            path = path[4:] or "/"
        if (path.startswith("/fonts/") or path.startswith("/img/") or path.startswith("/js/")) and ".." not in path:
            fp = "/opt/sub-bot/static" + path
            if os.path.isfile(fp):
                data = open(fp, "rb").read()
                ct = ("image/jpeg" if path.endswith(".jpg") else "image/png" if path.endswith(".png")
                      else "application/javascript" if path.endswith(".js") else "font/otf")
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=604800")
                self.end_headers()
                self.wfile.write(data)
                return
            return self._send(404, "{}")
        self._send(200, HTML, "text/html")

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 20000:
                return self._send(413, "{}")
            req = json.loads(self.rfile.read(n))
            web = self.path.split("?")[0].startswith("/web")
            if web and str(req.get("action", "")).startswith("web_"):
                resp, sid, logout = web_login_api(req)
                if resp is None:
                    return self._send(400, "{}")
                hdrs = []
                if sid:
                    hdrs.append(("Set-Cookie", f"sid={sid}; Path=/cabinet; Max-Age={SESS_DAYS * 86400}; HttpOnly; Secure; SameSite=Lax"))
                if logout:
                    drop_session(self.headers.get("Cookie"))
                    hdrs.append(("Set-Cookie", "sid=; Path=/cabinet; Max-Age=0; HttpOnly; Secure; SameSite=Lax"))
                return self._send(200, json.dumps(resp, ensure_ascii=False), headers=hdrs)
            user = check_init_data(req.get("initData", ""))
            if not user and web:
                suid = session_uid(self.headers.get("Cookie"))
                if suid:
                    ent = load_users().get(suid, {})
                    user = {"id": int(suid), "first_name": (ent.get("profile") or {}).get("name", ""), "username": ent.get("username", "")}
            rk = os.environ.get("ROUTER_KEY", "")
            if not user and rk and hmac.compare_digest(self.headers.get("X-Router-Key", ""), rk) and ADMINS:
                user = {"id": sorted(ADMINS)[0], "first_name": "router"}
            if not user:
                return self._send(401, json.dumps({"error": "login" if web else "Откройте приложение из Telegram"}))
            self._send(200, json.dumps(api(user, req), ensure_ascii=False))
        except ValueError as e:
            self._send(200, json.dumps({"error": str(e)}, ensure_ascii=False))
        except Exception as e:
            print("api error:", type(e).__name__, str(e)[:100], flush=True)
            self._send(500, json.dumps({"error": "Внутренняя ошибка"}))


def handle(msg):
    chat = msg["chat"]["id"]
    uid = str(msg["from"]["id"])
    pay = msg.get("successful_payment")
    if pay:
        stars = pay.get("total_amount", 0)
        tg("sendMessage", chat_id=chat, text=f"Спасибо! Дед благодарит за {stars} ⭐")
        who = msg["from"].get("username") or msg["from"].get("first_name") or uid
        for a in ADMINS:
            try:
                tg("sendMessage", chat_id=a, text=f"Донат: {stars} ⭐ от {who} (id {uid})")
            except Exception:
                pass
        print(f"donation uid={uid} stars={stars}", flush=True)
        return
    text = (msg.get("text") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text else ""
    users = load_users()
    allowed = uid in ADMINS or uid in users
    print(f"msg uid={uid} cmd={cmd} allowed={allowed}", flush=True)
    parts = text.split()
    if cmd == "/start" and len(parts) > 1 and parts[1].startswith("web_"):
        ent = WEBTOK.get(parts[1][4:])
        if not ent or ent["exp"] < time.time():
            tg("sendMessage", chat_id=chat, text="Ссылка для входа устарела. Начните вход на сайте заново.")
        elif not allowed:
            tg("sendMessage", chat_id=chat, text=f"Доступа пока нет. Ваш ID: {uid}\nПередайте его администратору.")
        else:
            ent["uid"] = uid
            tg("sendMessage", chat_id=chat, text="Вход подтверждён. Вернитесь в браузер.")
        return
    kb = {}
    if APP_URL and allowed:
        kb = {"reply_markup": json.dumps({"inline_keyboard": [[{"text": "Открыть приложение", "web_app": {"url": APP_URL}}]]})}
    if cmd == "/status" and allowed:
        tg("sendMessage", chat_id=chat, text="Состояние каналов:\n" + status_text() + f"\n\nПроблемы? Напишите {SUPPORT}")
    elif cmd == "/report" and uid in ADMINS:
        tg("sendMessage", chat_id=chat, text=build_report())
    elif cmd == "/sub" and allowed:
        found = [c for c in (find_client(e) for e in user_emails(users.get(uid, {}))) if c]
        text = "\n".join(f"{c['email']}: {sub_link(c)}" for c in found) if found else "Подписок ещё нет: создайте в приложении."
        tg("sendMessage", chat_id=chat, disable_web_page_preview="true", text=text, **kb)
    elif allowed:
        tg("sendMessage", chat_id=chat, text="Привет! Откройте приложение, чтобы получить подписку.", **kb)
    else:
        tg("sendMessage", chat_id=chat, text=f"Доступа пока нет. Ваш ID: {uid}\nПередайте его администратору.")


def poll():
    offset = 0
    while True:
        try:
            r = tg("getUpdates", offset=offset, timeout=30, allowed_updates=json.dumps(["message", "pre_checkout_query"]))
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                if "pre_checkout_query" in u:
                    try:
                        tg("answerPreCheckoutQuery", pre_checkout_query_id=u["pre_checkout_query"]["id"], ok="true")
                    except Exception as e:
                        print("precheckout error:", type(e).__name__, flush=True)
                if "message" in u:
                    try:
                        handle(u["message"])
                    except Exception as e:
                        print("handle error:", type(e).__name__, flush=True)
        except Exception as e:
            print("poll error:", type(e).__name__, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    threading.Thread(target=lambda: ThreadingHTTPServer(("127.0.0.1", 8099), H).serve_forever(), daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    threading.Thread(target=report_loop, daemon=True).start()
    threading.Thread(target=cleanup_loop, daemon=True).start()
    threading.Thread(target=monthly_loop, daemon=True).start()
    poll()
