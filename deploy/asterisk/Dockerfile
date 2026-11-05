# Asterisk 20 from Alpine's stable branch. Version 20 or newer is required: on older versions AudioSocket doesn't return
# to the dialplan when the app hangs up, so transfers can't work. The build fails if a module the call flow needs is missing.
FROM alpine:3.22
RUN apk add --no-cache asterisk asterisk-curl asterisk-sounds-en curl \
    && for module in app_audiosocket res_audiosocket func_curl func_shell res_pjsip; do \
         test -f "/usr/lib/asterisk/modules/$module.so" || { echo "Missing Asterisk module: $module" >&2; exit 1; }; \
       done \
    && mkdir -p /etc/asterisk /var/run/asterisk /var/lib/asterisk /var/log/asterisk /var/spool/asterisk \
    && chown -R asterisk:asterisk /etc/asterisk /var/run/asterisk /var/lib/asterisk /var/log/asterisk /var/spool/asterisk
COPY config/ /opt/voicerep/config/
COPY entrypoint.sh render-config.sh heartbeat.sh /opt/voicerep/
USER asterisk
ENTRYPOINT ["sh", "/opt/voicerep/entrypoint.sh"]
