#!/usr/bin/env python3
"""Subscription bot + Telegram Mini App (no payments).
Roles: admin (ADMIN_IDS) and allowed users (users.json). Allowed users can create
their own subscription once; admin can add/remove users, create/disable clients."""
import hashlib, hmac, json, os, socket, threading, time, urllib.request, urllib.parse
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
    return {"email": c["email"], "enable": c["enable"], "used": fmt(t.get("up", 0) + t.get("down", 0)),
            "limit": fmt(c["totalGB"]) if c.get("totalGB") else "без лимита",
            "expire": time.strftime("%d.%m.%Y", time.localtime(exp / 1000)) if exp > 0 else "без срока",
            "link": sub_link(c)}


def create_client(email, gb=0, days=0, comment=""):
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


def api(user, req):
    uid = str(user["id"])
    admin = uid in ADMINS
    users = load_users()
    allowed = admin or uid in users
    act = req.get("action")
    if act == "me":
        info = {"id": uid, "name": user.get("first_name", ""), "admin": admin, "allowed": allowed,
                "support": SUPPORT, "down": [n for n, s in STATUS.items() if not s["up"]]}
        if allowed:
            found = [find_client(e) for e in user_emails(users.get(uid, {}))]
            info["clients"] = [client_info(c) for c in found if c]
            info["limit"] = None if admin else SUBS_LIMIT
            info["proxy_limit"] = None if admin else PROXY_LIMIT
        return info
    if not allowed:
        return {"error": "Нет доступа. Передайте администратору ваш ID: " + uid}
    if act == "create_self":
        with LOCK:
            users = load_users()
            entry = users.get(uid, {})
            emails = user_emails(entry)
            if not admin and len(emails) >= SUBS_LIMIT:
                return {"error": f"Достигнут лимит: {SUBS_LIMIT} подписок"}
            n = len(emails) + 1
            email = "tg" + uid if n == 1 else f"tg{uid}-{n}"
            while find_client(email):
                n += 1
                email = f"tg{uid}-{n}"
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
        c = create_client(name, float(req.get("gb") or 0), int(req.get("days") or 0), "created by admin")
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


def note_down(name):
    PERIOD["incidents"][name] = PERIOD["incidents"].get(name, 0) + 1
    save_period()


def note_up(name, since):
    PERIOD["downtime"][name] = PERIOD["downtime"].get(name, 0) + max(0, time.time() - since)
    save_period()


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


HTML = open("/opt/sub-bot/index.html", encoding="utf-8").read()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if (path.startswith("/fonts/") or path.startswith("/img/")) and ".." not in path:
            fp = "/opt/sub-bot/static" + path
            if os.path.isfile(fp):
                data = open(fp, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg" if path.endswith(".jpg") else "font/otf")
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
            user = check_init_data(req.get("initData", ""))
            rk = os.environ.get("ROUTER_KEY", "")
            if not user and rk and hmac.compare_digest(self.headers.get("X-Router-Key", ""), rk) and ADMINS:
                user = {"id": sorted(ADMINS)[0], "first_name": "router"}
            if not user:
                return self._send(401, json.dumps({"error": "Откройте приложение из Telegram"}))
            self._send(200, json.dumps(api(user, req), ensure_ascii=False))
        except ValueError as e:
            self._send(200, json.dumps({"error": str(e)}, ensure_ascii=False))
        except Exception as e:
            print("api error:", type(e).__name__, str(e)[:100], flush=True)
            self._send(500, json.dumps({"error": "Внутренняя ошибка"}))


def handle(msg):
    chat = msg["chat"]["id"]
    uid = str(msg["from"]["id"])
    text = (msg.get("text") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text else ""
    users = load_users()
    allowed = uid in ADMINS or uid in users
    print(f"msg uid={uid} cmd={cmd} allowed={allowed}", flush=True)
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
            r = tg("getUpdates", offset=offset, timeout=30, allowed_updates=json.dumps(["message"]))
            for u in r.get("result", []):
                offset = u["update_id"] + 1
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
    poll()
