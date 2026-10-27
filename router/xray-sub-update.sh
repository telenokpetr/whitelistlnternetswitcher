#!/bin/sh
# Builds an Xray client config (local SOCKS5 + automatic fastest-profile selection)
# from a vless:// subscription. Needs: curl, jq, base64. Safe: tests the new config
# before replacing the old one, restarts Xray only when the config changed.
#
# /etc/xray-sub.url : subscription URL (one line)
# Env overrides (for tests): OUT, XRAY, NO_RESTART=1, SOCKS_PORT

SUB_FILE="${SUB_FILE:-/etc/xray-sub.url}"
OUT="${OUT:-/etc/xray-vk.json}"
XRAY="${XRAY:-/usr/bin/xray}"
SOCKS_PORT="${SOCKS_PORT:-10808}"
LAN_IP="${LAN_IP-192.168.1.1}"   # LAN address for per-profile Telegram SOCKS5 ports; empty = disabled
TPROXY_PORT="${TPROXY_PORT:-12345}"   # transparent inbound (0 = disabled)
GEO_DIR="${GEO_DIR:-/usr/share/xray}"
GEO_URL="${GEO_URL:-http://194.39.110.184:8081/lists-62543a2012ef/xray}"
TG_BASE="${TG_BASE:-10811}"       # first per-profile port: p1 -> 10811, p2 -> 10812, ...
NAMES="/tmp/xray-ports.$$"
: > "$NAMES"
TMP="/tmp/xray-sub.$$.json"
TAG="xray-sub"

log() { logger -t "$TAG" "$*" 2>/dev/null; echo "$*"; }
fail() { log "ERROR: $*"; rm -f "$TMP" "$NAMES"; exit 1; }

command -v jq >/dev/null || fail "jq is not installed (opkg install jq)"
[ -s "$SUB_FILE" ] || fail "subscription URL file $SUB_FILE is missing"
SUB_URL=$(head -n1 "$SUB_FILE" | tr -d '\r\n ')

# DNS-independent: pin our own host to its IP (the router DNS may be broken by transparent proxying)
SUB_RESOLVE="${SUB_RESOLVE:-static.scout-service.com:443:194.39.110.184}"
# routing data (small RU-only geoip/geosite built on our server); keep the old copy on failure
mkdir -p "$GEO_DIR"
GEO_CHANGED=0
for f in geoip.dat geosite.dat; do
  if curl -fsS -m 60 -o "/tmp/$f.new" "$GEO_URL/$f" && [ "$(wc -c < "/tmp/$f.new")" -gt 1000 ]; then
    if ! cmp -s "/tmp/$f.new" "$GEO_DIR/$f" 2>/dev/null; then mv "/tmp/$f.new" "$GEO_DIR/$f"; GEO_CHANGED=1; else rm -f "/tmp/$f.new"; fi
  else
    log "geo: could not refresh $f, keeping the old one"; rm -f "/tmp/$f.new"
  fi
done
export XRAY_LOCATION_ASSET="$GEO_DIR"

raw=$(curl -fsS -m 30 --resolve "$SUB_RESOLVE" "$SUB_URL") || raw=$(curl -fsS -m 30 "$SUB_URL") || fail "cannot download the subscription"
links=$(printf '%s' "$raw" | base64 -d 2>/dev/null | tr -d '\r')
[ -n "$links" ] || fail "subscription is empty or not base64"

urldecode() {
  printf '%s' "$1" | LC_ALL=C awk 'BEGIN{for(i=1;i<256;i++)h[sprintf("%02X",i)]=sprintf("%c",i)}
    {s=$0; o=""; while(match(s,/%[0-9A-Fa-f][0-9A-Fa-f]/)){o=o substr(s,1,RSTART-1) h[toupper(substr(s,RSTART+1,2))]; s=substr(s,RSTART+3)} print o s}'
}
getq() { printf '%s' "$Q" | tr '&' '\n' | sed -n "s/^$1=//p" | head -n1; }

outs="[]"
NL_TAGS="[]"
SE_TAGS="[]"
FI_TAGS="[]"
PROFILES="[]"
i=0
for link in $links; do
  case "$link" in vless://*) ;; *) continue ;; esac
  rest=${link#vless://}
  uuid=${rest%%@*}
  hp=${rest#*@}
  hostport=${hp%%\?*}
  host=${hostport%:*}
  port=${hostport##*:}
  Q=${hp#*\?}; Q=${Q%%#*}

  name=$(urldecode "${hp##*#}")
  type=$(getq type); sec=$(getq security)
  case "$type" in xhttp|tcp) ;; *) continue ;; esac
  case "$sec" in tls|reality) ;; *) continue ;; esac

  sni=$(urldecode "$(getq sni)");   fp=$(urldecode "$(getq fp)")
  alpn=$(urldecode "$(getq alpn)"); hdr=$(urldecode "$(getq host)")
  path=$(urldecode "$(getq path)"); mode=$(urldecode "$(getq mode)")
  extra=$(urldecode "$(getq extra)"); flow=$(urldecode "$(getq flow)")
  pbk=$(urldecode "$(getq pbk)");   sid=$(urldecode "$(getq sid)")
  spx=$(urldecode "$(getq spx)");   pqv=$(urldecode "$(getq pqv)")

  i=$((i+1))
  ob=$(jq -n --arg tag "p$i" --arg addr "$host" --argjson port "$port" --arg id "$uuid" \
     --arg flow "$flow" --arg type "$type" --arg sec "$sec" --arg sni "$sni" --arg fp "$fp" \
     --arg alpn "$alpn" --arg hdr "$hdr" --arg path "$path" --arg mode "$mode" --arg extra "$extra" \
     --arg pbk "$pbk" --arg sid "$sid" --arg spx "$spx" --arg pqv "$pqv" '
    {tag:$tag, protocol:"vless",
     settings:{vnext:[{address:$addr, port:$port,
        users:[{id:$id, encryption:"none"} + (if $flow!="" then {flow:$flow} else {} end)]}]},
     streamSettings:(
       {network:$type, security:$sec, sockopt:{mark:255}}
       + (if $sec=="tls" then {tlsSettings:({serverName:$sni, fingerprint:(if $fp=="" then "chrome" else $fp end)}
            + (if $alpn!="" then {alpn:($alpn|split(","))} else {} end))} else {} end)
       + (if $sec=="reality" then {realitySettings:({serverName:$sni, fingerprint:(if $fp=="" then "chrome" else $fp end),
            publicKey:$pbk, shortId:$sid, spiderX:(if $spx=="" then "/" else $spx end)}
            + (if $pqv!="" then {mldsa65Verify:$pqv} else {} end))} else {} end)
       + (if $type=="xhttp" then {xhttpSettings:({path:$path, mode:(if $mode=="" then "auto" else $mode end)}
            + (if $hdr!="" then {host:$hdr} else {} end)
            + (if $extra!="" then ($extra|fromjson|del(.sessionIDKey,.sessionIDPlacement)) else {} end))} else {} end)
     )}') || { log "skip profile $host (cannot build)"; i=$((i-1)); continue; }
  outs=$(printf '%s' "$outs" | jq --argjson ob "$ob" '. + [$ob]')
  PROFILE_NAME=$name
  [ -n "$PROFILE_NAME" ] || PROFILE_NAME="profile $i"
  PROFILES=$(printf '%s' "$PROFILES" | jq --arg tag "p$i" --arg name "$PROFILE_NAME" '. + [{tag:$tag,name:$name}]')
  case "$PROFILE_NAME" in
    *Нидерланд*|*Netherlands*|*Nederland*|*🇳🇱*)
      NL_TAGS=$(printf '%s' "$NL_TAGS" | jq --arg tag "p$i" '. + [$tag]') ;;
    *Швеци*|*Sweden*|*Sverige*|*🇸🇪*)
      SE_TAGS=$(printf '%s' "$SE_TAGS" | jq --arg tag "p$i" '. + [$tag]') ;;
    *Хельсинк*|*Helsinki*|*Финлянди*|*Finland*|*🇫🇮*)
      FI_TAGS=$(printf '%s' "$FI_TAGS" | jq --arg tag "p$i" '. + [$tag]') ;;
  esac
  printf '%s|%s\n' "$((TG_BASE+i-1))" "$PROFILE_NAME" >> "$NAMES"
done

[ "$i" -gt 0 ] || fail "no usable vless profiles found in the subscription"

cp "$NAMES" /etc/xray-vk.candidates && chmod 600 /etc/xray-vk.candidates || fail "cannot save available profiles"
PRIORITY_FILE="${PRIORITY_FILE:-/etc/xray-vk.priority.json}"
if [ -s "$PRIORITY_FILE" ]; then
  PRIORITY_NAMES=$(jq -ce 'select(type=="array" and length==3 and all(.[]; type=="string"))' "$PRIORITY_FILE" 2>/dev/null) || fail "priority settings invalid; old config kept"
else
  if [ "$(printf '%s' "$NL_TAGS" | jq 'length')" -eq 0 ] || [ "$(printf '%s' "$SE_TAGS" | jq 'length')" -eq 0 ] || [ "$(printf '%s' "$FI_TAGS" | jq 'length')" -eq 0 ]; then
    log "ВЫБЕРИТЕ: укажите в панели три доступных профиля; прежняя конфигурация сохранена"
    rm -f "$TMP" "$NAMES"
    exit 2
  fi
  PRIORITY_NAMES=$(jq -cn --argjson p "$PROFILES" --argjson nl "$NL_TAGS" --argjson se "$SE_TAGS" --argjson fi "$FI_TAGS" '[($p[]|select(.tag==$nl[0])|.name),($p[]|select(.tag==$se[0])|.name),($p[]|select(.tag==$fi[0])|.name)]')
fi
PRIORITY_TAGS=$(jq -cn --argjson p "$PROFILES" --argjson names "$PRIORITY_NAMES" '[$names[] as $n | [$p[]|select(.name==$n)|.tag] | if length==1 then .[0] else empty end]')
if [ "$(printf '%s' "$PRIORITY_TAGS" | jq 'length')" -ne 3 ]; then
  log "ВЫБЕРИТЕ: прежние профили отсутствуют или неоднозначны; выберите три доступных профиля"
  rm -f "$TMP" "$NAMES"
  exit 2
fi
P1=$(printf '%s' "$PRIORITY_TAGS" | jq -r '.[0]')
P2=$(printf '%s' "$PRIORITY_TAGS" | jq -r '.[1]')
P3=$(printf '%s' "$PRIORITY_TAGS" | jq -r '.[2]')

jq -n --argjson outs "$outs" --arg p1 "$P1" --arg p2 "$P2" --arg p3 "$P3" --argjson sp "$SOCKS_PORT" --arg lan "$LAN_IP" --argjson base "$TG_BASE" --argjson tp "$TPROXY_PORT" '
  ($outs | map(.tag)) as $tags |
  ($tp > 0) as $tpon |
  {
  log:{loglevel:"warning"},
  inbounds:([{tag:"socks-in", listen:"127.0.0.1", port:$sp, protocol:"socks", settings:{udp:true}}]
    + (if $lan != "" then ($tags | to_entries | map({tag:("tg-" + .value), listen:$lan, port:($base + .key),
         protocol:"socks", settings:{udp:true, ip:$lan}})) else [] end)
    + (if $tpon then [{tag:"tproxy-in", listen:"0.0.0.0", port:$tp, protocol:"dokodemo-door",
         settings:{network:"tcp,udp", followRedirect:true},
         streamSettings:{sockopt:{tproxy:"tproxy"}},
         sniffing:{enabled:true, destOverride:["http","tls","quic"]}}] else [] end)),
  outbounds:($outs + [
    {tag:"fallback-2", protocol:"loopback", settings:{inboundTag:"route-2"}},
    {tag:"fallback-3", protocol:"loopback", settings:{inboundTag:"route-3"}},
    {tag:"direct", protocol:"freedom", streamSettings:{sockopt:{mark:255}}}
  ]),
  observatory:{subjectSelector:["p"], probeUrl:"https://www.gstatic.com/generate_204", probeInterval:"1m", enableConcurrency:true},
  routing:{domainStrategy:"AsIs",
    balancers:[
      {tag:"priority-1", selector:[$p1], strategy:{type:"leastPing"}, fallbackTag:"fallback-2"},
      {tag:"priority-2", selector:[$p2], strategy:{type:"leastPing"}, fallbackTag:"fallback-3"},
      {tag:"priority-3", selector:[$p3], strategy:{type:"leastPing"}, fallbackTag:$p3}
    ],
    rules:(
      [{type:"field", inboundTag:["route-2"], balancerTag:"priority-2"},
       {type:"field", inboundTag:["route-3"], balancerTag:"priority-3"}]
      + (if $tpon then [
        {type:"field", inboundTag:["tproxy-in"], protocol:["bittorrent"], outboundTag:"direct"},
        {type:"field", inboundTag:["tproxy-in"], ip:["geoip:private"], outboundTag:"direct"},
        {type:"field", inboundTag:["tproxy-in"],
         domain:["geosite:category-ru","geosite:ru-available-only-inside","domain:ru","domain:su","domain:xn--p1ai"], outboundTag:"direct"},
        {type:"field", inboundTag:["tproxy-in"], ip:["geoip:ru"], outboundTag:"direct"},
        {type:"field", inboundTag:["tproxy-in"], balancerTag:"priority-1"}] else [] end)
      + ($tags | map({type:"field", inboundTag:[("tg-" + .)], outboundTag:.}))
      + [{type:"field", inboundTag:["socks-in"], balancerTag:"priority-1"}])}
}' > "$TMP" || fail "cannot assemble the config"


"$XRAY" run -test -c "$TMP" >/tmp/xray-sub.test 2>&1 || { log "$(tail -3 /tmp/xray-sub.test | tr '\n' ' ')"; fail "new config did not pass xray -test, old config kept"; }

printf '%s\n' "$PRIORITY_NAMES" > /etc/xray-vk.priority.json
chmod 600 /etc/xray-vk.priority.json
# The subscription randomises shortId/spiderX on every request, so compare a signature
# that ignores them; otherwise Xray would be restarted for nothing on each run.
SIG_NEW=$(jq -S 'del(.. | .shortId?, .spiderX?)' "$TMP" | md5sum | cut -d' ' -f1)
SIG_OLD=$(cat "$OUT.sig" 2>/dev/null)
if [ -f "$OUT" ] && [ "$SIG_NEW" = "$SIG_OLD" ] && [ "$GEO_CHANGED" = "0" ]; then
  log "config unchanged ($i profiles)"
  rm -f "$TMP" "$NAMES"; exit 0
fi

mv "$TMP" "$OUT" && chmod 600 "$OUT" && printf '%s' "$SIG_NEW" > "$OUT.sig"
[ -n "$LAN_IP" ] && cp "$NAMES" "${OUT%.json}.ports" 2>/dev/null
log "config updated: $i profiles"
if [ -z "$NO_RESTART" ] && [ -x /etc/init.d/xray-vk ]; then
  /etc/init.d/xray-vk restart
fi
rm -f "$NAMES"
exit 0
