#!/bin/sh
# Mirrors public routing lists to a local web directory so that the router
# does not need GitHub. Keeps the previous copy if a download fails or looks wrong.
DEST="${DEST:?DEST directory is required}"
mkdir -p "$DEST" || exit 1
TMP=$(mktemp -d)
OK=0; BAD=0

fetch() {  # name url min_bytes
  name="$1"; url="$2"; min="$3"
  if curl -fsSL -m 120 -o "$TMP/$name" "$url" && [ "$(wc -c < "$TMP/$name")" -ge "$min" ]; then
    mv "$TMP/$name" "$DEST/$name" && chmod 644 "$DEST/$name" && OK=$((OK+1))
  else
    echo "FAILED: $name ($url), keeping the old copy"; BAD=$((BAD+1))
  fi
}

# sing-box rule sets (as referenced in the Forkop section)
fetch ru-blocked.srs            https://raw.githubusercontent.com/runetfreedom/russia-blocked-geoip/release/srs/ru-blocked.srs 1000
fetch ru-blocked-community.srs  https://raw.githubusercontent.com/runetfreedom/russia-blocked-geoip/release/srs/ru-blocked-community.srs 500
fetch geoip-ru.srs              https://github.com/MetaCubeX/meta-rules-dat/raw/sing/geo/geoip/ru.srs 1000

# built-in service subnet lists
for s in discord meta twitter telegram; do
  fetch "$s-ipv4.lst" "https://raw.githubusercontent.com/itdoginfo/allow-domains/main/Subnets/IPv4/$s.lst" 20
  fetch "$s-ipv6.lst" "https://raw.githubusercontent.com/itdoginfo/allow-domains/main/Subnets/IPv6/$s.lst" 20
done

rm -rf "$TMP"
date -u +%FT%TZ > "$DEST/updated.txt"
echo "mirror done: ok=$OK failed=$BAD"
[ "$BAD" -eq 0 ]
