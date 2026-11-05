#!/bin/sh
set -eu

sh /opt/voicerep/render-config.sh /opt/voicerep/config /etc/asterisk
sh /opt/voicerep/heartbeat.sh &
exec asterisk -f
