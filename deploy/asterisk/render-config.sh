#!/bin/sh
# Writes Asterisk's config files into TARGET, from the files in SOURCE and the settings in the environment.
# Usage: render-config.sh SOURCE TARGET
set -eu

src=$1
dst=$2

: "${VR_ASTERISK_TOKEN:?Set VR_ASTERISK_TOKEN in .env. The API uses the same value.}"
: "${ASTERISK_PUBLIC_IP:?Set ASTERISK_PUBLIC_IP in .env to this server's Elastic IP.}"
: "${ASTERISK_LOCAL_NET:?Set ASTERISK_LOCAL_NET in .env to this server's VPC range.}"
: "${SIP_PROVIDER_HOST:?Set SIP_PROVIDER_HOST in .env to your SIP provider's address.}"
: "${SIP_PROVIDER_IPS:?Set SIP_PROVIDER_IPS in .env to your SIP provider's signaling IPs, separated by commas.}"

cp "$src"/*.conf "$dst"/
sed -e "s|@ASTERISK_PUBLIC_IP@|$ASTERISK_PUBLIC_IP|g" \
    -e "s|@ASTERISK_LOCAL_NET@|$ASTERISK_LOCAL_NET|g" \
    -e "s|@SIP_PROVIDER_HOST@|$SIP_PROVIDER_HOST|g" \
    "$src/pjsip.conf.template" > "$dst/pjsip.conf"

# The template ends with the section these belong to.
for ip in $(printf '%s' "$SIP_PROVIDER_IPS" | tr ',' ' '); do
    printf 'match=%s\n' "$ip" >> "$dst/pjsip.conf"
done
