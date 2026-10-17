#!/bin/sh
# Parental control (weekly time budget counted only while traffic flows) + Wi-Fi schedule.
D=/etc/pc; R=/tmp/pc; CFG=$D/config.json; ST=$R/state.json
TICK=10
mkdir -p "$D" "$R"
[ -s "$CFG" ] || echo '{"wifi":{"enabled":false,"off":"23:00","on":"07:00"},"limits":{},"names":{},"threshold_kb":30}' > "$CFG"
[ -s "$ST" ] || { cp "$D/state.json" "$ST" 2>/dev/null || echo '{"week":"","used":{},"bonus":{}}' > "$ST"; }

persist() { cmp -s "$ST" "$D/state.json" 2>/dev/null || cp "$ST" "$D/state.json"; }
trap 'persist; exit 0' TERM INT

mac_ip() {
	ip neigh show dev br-lan | awk -v m="$1" '$1 ~ /\./ && tolower($3)==m {print $1; exit}'
}
lease_ip() {
	awk -v m="$1" 'tolower($2)==m {print $3; exit}' /tmp/dhcp.leases 2>/dev/null
}
tomin() {
	h=${1%:*}; m=${1#*:}
	h=$(echo "$h" | sed 's/^0*//'); m=$(echo "$m" | sed 's/^0*//')
	echo $(( ${h:-0} * 60 + ${m:-0} ))
}

lastt=$(date +%s); sigr="#"; lastb="x"; lasta="x"; n=0
while :; do
	sleep $TICK
	now=$(date +%s); el=$((now - lastt)); lastt=$now
	[ "$el" -gt 30 ] && el=$TICK
	[ "$el" -lt 1 ] && el=1
	wk=$(date +%G-%V)
	if [ "$(jq -r .week "$ST" 2>/dev/null)" != "$wk" ]; then
		echo "{\"week\":\"$wk\",\"used\":{},\"bonus\":{}}" > "$ST"; persist
	fi

	# ---- per-device counters
	macs=$(jq -r '.limits|to_entries[]|select(.value.week_min>0)|.key' "$CFG" 2>/dev/null)
	db=$(jq -r '.default_block // false' "$CFG")
	up=""; dn=""; sig=""
	for m in $macs; do
		ip=$(mac_ip "$m"); [ -n "$ip" ] || ip=$(lease_ip "$m")
		[ -n "$ip" ] || continue
		sig="$sig $m=$ip"
		up="$up
		iifname \"br-lan\" ip saddr $ip counter comment \"$m\""
		dn="$dn
		oifname \"br-lan\" ip daddr $ip counter comment \"$m\""
	done
	sig="$sig db=$db"
	DBR=""
	if [ "$db" = "true" ]; then
		DBR='
		iifname "br-lan" ether saddr != @allowed ip daddr != { 192.168.1.1, 255.255.255.255 } drop'
	fi
	if [ "$sig" != "$sigr" ]; then
		nft delete table inet pc 2>/dev/null
		nft -f - <<EOF
table inet pc {
	set blocked { type ether_addr; }
	set allowed { type ether_addr; }
	chain pre {
		type filter hook prerouting priority -300; policy accept;
		iifname "br-lan" ether saddr @blocked ip daddr != { 192.168.1.1, 255.255.255.255 } drop$DBR$up
	}
	chain post {
		type filter hook postrouting priority 300; policy accept;$dn
	}
}
EOF
		sigr="$sig"; rm -f "$R"/last.*; lastb="x"; lasta="x"
	fi

	thr=$(( $(jq -r '.threshold_kb // 30' "$CFG") * 1024 ))
	nft -j list table inet pc 2>/dev/null | jq -r '.nftables[]|select(.rule)|.rule|select(.comment)|"\(.comment) \([.expr[]|select(.counter)|.counter.bytes]|add)"' \
		| awk '{s[$1]+=$2} END{for(m in s) print m, s[m]}' > "$R/cur"
	: > "$R/active.new"
	while read -r m b; do
		[ -n "$m" ] || continue
		f="$R/last.$(echo "$m" | tr -d :)"
		l=$(cat "$f" 2>/dev/null || echo 0)
		d=$((b - l)); [ "$d" -lt 0 ] && d=$b
		echo "$b" > "$f"
		if [ "$d" -ge "$thr" ]; then
			echo "$m" >> "$R/active.new"
			jq --arg m "$m" --argjson s "$el" '.used[$m]=((.used[$m]//0)+$s)' "$ST" > "$ST.t" && mv "$ST.t" "$ST"
		fi
	done < "$R/cur"
	mv "$R/active.new" "$R/active"

	bl=$(jq -r --slurpfile st "$ST" '.limits|to_entries[]|select(.value.week_min>0 and ((.value.week_min*60)+($st[0].bonus[.key]//0)) <= ($st[0].used[.key]//0))|.key' "$CFG" | sort | tr '\n' ' ')
	if [ "$bl" != "$lastb" ]; then
		nft flush set inet pc blocked
		[ -n "$bl" ] && nft add element inet pc blocked "{ $(echo $bl | sed 's/ /, /g') }"
		lastb="$bl"
	fi
	echo "$bl" > "$R/blocked"
	al=$(jq -r '.limits|keys|sort|join(" ")' "$CFG")
	if [ "$al" != "$lasta" ]; then
		nft flush set inet pc allowed
		[ -n "$al" ] && nft add element inet pc allowed "{ $(echo $al | sed 's/ /, /g') }"
		lasta="$al"
	fi

	n=$((n + 1))
	if [ $((n % 30)) -eq 0 ]; then persist; fi
done
