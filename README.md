# whitelist internet switcher

Bypass of mobile-internet whitelists through Russian CDNs (VK Cloud / Yandex Cloud) with 3x-ui on foreign VPS,
a Telegram bot / Mini App for subscriptions, and an OpenWrt (RouteRich) router running plain Xray with
"RU direct, everything else through the tunnel" routing, plus a small router panel (per-device bypass,
weekly parental time limits counted only while traffic flows, subscription management).

- `server/` — Telegram bot + Mini App, list mirror and geo slimming scripts (run on the VPS).
- `router/` — Xray config generator from a vless subscription, transparent redirect (tproxy), fail-open guard.
- `panel/` — router panel: daemon, init script, uhttpd CGI API and web UI.
- `INSTRUCTION.md` — architecture, usage, troubleshooting (Russian).
- `openwrt/` — build and installation instructions for the opkg package and its LuCI Services entry.

Placeholders to fill before use: `SERVER_IP`, `YOUR_DOMAIN`, `LISTS_PATH`, `TGAPP_PATH`
(private URL paths on your server). No tokens or passwords are stored in this repository:
the bot reads them from `/etc/sub-bot.env`, the router panel from `/etc/pc/`.
