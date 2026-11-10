#!/bin/sh
# Fail-open guard: if the redirect rules exist but Xray is not listening, remove them
# so the redirected devices go straight to the internet instead of being blackholed.
# When Xray is back (procd respawn) and the guard itself removed the rules, put them back.
PORT=12345
FLAG=/tmp/xray-tp.guard-removed
LISTEN=0
netstat -lnt 2>/dev/null | grep -q ":$PORT " && LISTEN=1
if nft list table ip xray_tp >/dev/null 2>&1; then
	if [ "$LISTEN" = 0 ]; then
		logger -t xray-tp-guard "Xray is not listening on $PORT, removing the redirect rules"
		: > "$FLAG"
		/etc/init.d/xray-tp stop
	fi
elif [ "$LISTEN" = 1 ] && [ -f "$FLAG" ] && [ -e /etc/rc.d/S96xray-tp ]; then
	rm -f "$FLAG"
	logger -t xray-tp-guard "Xray is back on $PORT, restoring the redirect rules"
	/etc/init.d/xray-tp start
fi
exit 0
