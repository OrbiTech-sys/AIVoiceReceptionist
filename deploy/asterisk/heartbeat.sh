#!/bin/sh
# Tells the API every minute that Asterisk is alive. When these stop, the API alerts the businesses it affects.
while sleep 60; do
    if asterisk -rx "core show version" >/dev/null 2>&1; then
        printf 'token=%s' "$VR_ASTERISK_TOKEN" |
            curl -fsS --max-time 5 --data @- http://127.0.0.1:8000/api/telephony/asterisk/heartbeat >/dev/null 2>&1 || true
    fi
done
