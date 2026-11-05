Connecting real phone calls
===========================
All businesses share this one Asterisk and one SIP account. There's nothing per-business to configure here:
a platform admin assigns each business a number under Platform admin -> Businesses.

1. Buy a US number from a SIP provider, and set up a SIP trunk there:
   - Send inbound calls to sip:YOUR_ELASTIC_IP:5060
   - Allow outbound calls from YOUR_ELASTIC_IP (IP authentication, no password)
   - Allow G.711 (ulaw/alaw)
   - Set a failover number if the provider offers one, for when this server can't be reached
   - Send the dialled number in E.164 format if there's a choice

2. In the AWS security group, allow only:
   - UDP 5060 from the provider's signaling IPs
   - UDP 10000-10200 for call audio (config/rtp.conf)
   Don't open 9092 or 8000.

3. Add these to the .env file next to docker-compose.yml:
     COMPOSE_PROFILES=phone             (add to the existing value if it's already set, e.g. llm,phone)
     VR_ASTERISK_TOKEN=                 (make one with: openssl rand -hex 32)
     ASTERISK_PUBLIC_IP=                (your Elastic IP)
     ASTERISK_LOCAL_NET=172.31.0.0/16   (your VPC range; this is the AWS default)
     SIP_PROVIDER_HOST=                 (the provider's SIP address)
     SIP_PROVIDER_IPS=                  (the provider's signaling IPs, separated by commas)

4. Start it:
     docker compose up -d --build

5. Block the Asterisk-only API routes from the internet. In nginx, inside the server block:
     location /api/telephony/asterisk/ { return 404; }
   Asterisk calls the API on 127.0.0.1 directly, so it doesn't go through nginx.

6. Assign the number under Platform admin -> Businesses, then call it from another phone.
   The business shows Connected after that first real call.

Checking on it
--------------
  docker compose logs -f asterisk api
  docker compose exec asterisk asterisk -rx "pjsip show endpoints"
  docker compose exec asterisk asterisk -rx "pjsip set logger on"     (shows SIP messages)
  docker compose exec asterisk asterisk -rx "rtp set debug on"        (shows whether audio is flowing)

What you see                        Likely cause
Nothing reaches Asterisk            Security group, or the provider's destination address
Call arrives but is rejected        SIP_PROVIDER_IPS doesn't include the address it came from
"Please try again later" message    The API refused the call, or wasn't reachable; check the api logs
No audio, or one-way audio          RTP ports, or ASTERISK_PUBLIC_IP / ASTERISK_LOCAL_NET
Call drops after about 30 seconds   Usually ASTERISK_PUBLIC_IP / ASTERISK_LOCAL_NET
