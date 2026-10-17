#!/bin/sh
# Fail-open guard: if the redirect rules exist but Xray is not listening, remove them
# so the redirected devices go straight to the internet instead of being blackholed.
PORT=12345
if nft list table ip xray_tp >/dev/null 2>&1; then
	if ! netstat -lnt 2>/dev/null | grep -q ":$PORT "; then
		logger -t xray-tp-guard "Xray is not listening on $PORT, removing the redirect rules"
		/etc/init.d/xray-tp stop
	fi
fi
exit 0
