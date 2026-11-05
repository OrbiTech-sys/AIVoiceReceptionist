# OrbiTech AI Receptionist

Multi-tenant AI voice receptionist SaaS: answers calls, answers business questions, books / reschedules / cancels appointments, and notifies the owner. Speech recognition, voice activity detection and speech synthesis run **locally on open-source models** — no paid AI APIs and no API keys.

**Stack:** Next.js 16 + React 19 + TypeScript + Tailwind (PWA) · FastAPI · SQLAlchemy/Alembic · PostgreSQL (SQLite for quick local dev) · faster-whisper · Silero VAD · Kokoro-82M TTS · Asterisk (AudioSocket) for live phone calls · Web Push (VAPID).

## Quick start (local, no Docker)

Requires Node 22+, Python 3.12+.

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Linux/macOS: .venv/bin/pip
.venv/Scripts/alembic upgrade head
.venv/Scripts/python seed.py                          # demo business + admin (dev only)
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# 2. Frontend (new terminal, repo root)
npm install
npm run dev                                           # http://localhost:3000
```

Sign in with the seeded demo owner `demo@example.com` / `DemoOwner2026!` (demo business is labelled as demo data). Platform admin: `admin@example.com` / `PlatformAdmin2026!`. These are dev-only seed credentials; `seed.py` refuses to run when `VR_ENV=production`.

**Install speech models:** with Docker Compose the API downloads Whisper (`base.en`, ~145 MB) and Kokoro (~325 MB + voices) by itself in the background on first start (`VR_AUTO_INSTALL_MODELS`, on in `docker-compose.yml`); watch progress under *Platform admin*. Without Docker, either set `VR_AUTO_INSTALL_MODELS=true` in `backend/.env`, or sign in as the platform admin → *Platform admin* → *Download & verify*. Files are checksum-verified and kept in the `appdata` volume, so later restarts and redeploys don't download again. Then open *AI Receptionist → Test call*.

## Docker Compose

```bash
cp backend/.env.example .env   # set VR_SECRET_KEY, DB_PASSWORD
docker compose up --build
docker compose --profile phone up   # also start Asterisk
docker compose --profile llm up -d  # also start the optional LLM (see "Optional LLM" below; needs ~2 GB more RAM)
```
> The compose file and Dockerfiles were written but **not built or run** in the development environment (Docker wasn't installed there). Treat as unverified until you run it once.

### Optional LLM (off by default)
Calls work without it: the conversation engine is rule-based. The LLM (Qwen2.5-1.5B via llama.cpp) only *classifies* an unusual phrasing into a fixed set of intents; its output is validated and ignored if invalid. It needs about 2 GB of RAM on top of the rest, so enable it only on a server with room (roughly 8 GB total alongside the speech models; this estimate has not been measured).

1. *Platform admin* → *Download & verify* on **Qwen2.5-1.5B-Instruct** (~1.1 GB).
2. In `.env` (next to `docker-compose.yml`) set `VR_LLM_URL=http://llm:8081` and `COMPOSE_PROFILES=llm`, so every deploy keeps the `llm` service running.
3. `docker compose up -d`. *AI Receptionist* shows the LLM as **Ready** once it answers `/health`; **Unreachable** means the URL is set but nothing answers (e.g. the model file isn't downloaded yet).

To turn it off again, remove both lines from `.env`, run `docker compose rm -sf llm` (stops and removes that container), then `docker compose up -d`. The downloaded model can be removed on the *Platform admin* page.

## What works locally vs. what needs outside infrastructure

| Works with no outside services | Needs external infrastructure |
|---|---|
| Accounts, tenants, roles, onboarding, settings | **Real phone calls**: a SIP provider (number + per-minute fees) and an Asterisk server |
| Appointment calendar, booking, conflict prevention, reminders (server-side) | **Push alerts** to phones: HTTPS in production + user permission (iOS: install to Home Screen) |
| Browser voice demo (mic → VAD → Whisper → dialog → Kokoro → speaker), typed fallback | **E-mail alerts**: your own SMTP server |
| In-app notifications, sounds, custom sound upload | Optional LLM classifier: opt-in `llm` compose profile (llama.cpp, ~2 GB more RAM) |

### Cost transparency
1. **Software / AI inference:** free. MIT/Apache models, no per-call fees.
2. **Hosting / hardware:** you pay for a server. CPU-only is fine for the demo; measured here (8 threads, no GPU): Whisper base.en transcribes a 3 s utterance in ~1 s; Kokoro synthesizes at ~0.6× real time, first audio ≈ 1–3 s. Concurrent live calls need more cores.
3. **Telephony:** a phone number, SIP trunk and call minutes are paid by you to your provider. Entering a number in the UI does **not** connect it; you must configure SIP/forwarding. Forwarding from an existing number is set up with your carrier (options and costs vary); porting takes days.
4. **Optional:** SMTP, SMS (not implemented), hosting domain/TLS.

### Live phone setup (summary)
*Integrations → Phone line*: enter your DID, SIP host and credentials (password stored Fernet-encrypted, never shown again), generate the Asterisk `pjsip.conf` / `extensions.conf`, load them in Asterisk, and call the number. Status becomes **Connected only after a real call reaches the receptionist.** Not tested against a real SIP provider in development — only the AudioSocket adapter (framing, unknown-call rejection, hang-up, ending a call, transfer signalling, silence frames, failures) is covered by automated tests. The dialplan only hands a call to the receptionist when `precall` answers `ok`; it answers `off` when the receptionist is turned off and `unavailable` when the speech models aren't ready.

## Licensing notes (verify before commercial deployment)
- faster-whisper, Whisper weights, Silero VAD, onnxruntime: MIT. Kokoro-82M weights: Apache-2.0; `kokoro-onnx`: MIT. Qwen2.5 (optional): Apache-2.0.
- **`kokoro-onnx` phonemizes through espeak-ng (GPL-3.0)** via `phonemizer`/`espeakng-loader`. If you distribute the software, review GPL obligations (or swap in a different phonemizer/TTS). Hosting it as a service has different obligations than distributing it — get advice.
- Asterisk is GPL-2.0 and runs as a separate process.
- `soxr` (audio resampling) is LGPL-2.1-or-later, used as an unmodified library.

## Security & privacy
Argon2id passwords, server-side sessions in HttpOnly cookies, CSRF header on mutations, per-route rate limits, tenant id always taken from the server session and membership (never from the browser), tenant-scoped queries, Fernet-encrypted secrets, magic-byte audio upload validation, origin checks on WebSockets, audit events, retention purge job, call recording **off by default** (requires a consent message). Callers' speech is data only: the conversation engine can only invoke validated backend tools. In production you must provide HTTPS, `VR_ENV=production`, a strong `VR_SECRET_KEY`, `VR_COOKIE_SECURE=true`, PostgreSQL backups (`pg_dump` nightly; restore with `psql`) and monitoring (`/api/health`). You are responsible for call-recording, privacy and AI-disclosure law compliance; a checkbox does not make you compliant.

## Tests
```bash
cd backend && .venv/Scripts/python -m pytest -q     # 71 tests (the real-audio one is skipped until the speech models are installed)
npx tsc --noEmit && npx eslint . && npm run build
```

### Test report (last run in development environment)
- Backend: **70 passed, 1 skipped** (6 consecutive full runs, on Python 3.13 without the speech packages; the skipped test is the real-audio one). Covers phone call start, audio and hang-up behaviour, auth/CSRF/rate limit, tenant isolation, staff role, hours validation, availability (hours, breaks, buffers, holidays, notice, staff calendars), concurrent double-booking (6 threads → 1 booking), reschedule/cancel/status transitions, policy notice, contact de-duplication, DST/time-zone handling, reminder uniqueness and exactly-once delivery across 4 workers, upload validation, telephony secrets/dialplan auth/AudioSocket failures, LLM output validation, dialog safety (emergency, injection, medical advice, no invented prices), the 10-step booking scenario over WebSocket, migrations, and a **real-audio** test (synthesized speech → Silero → Whisper → dialog → Kokoro) when models are installed.
- Frontend: `tsc --noEmit`, `eslint` and `next build` pass. UI was exercised manually in a browser pane: login, overview, and a full typed booking conversation that persisted an appointment, notification and reminder.
- **Not verified:** real microphone capture (blocked in the test browser — use *Test microphone* in the UI), audible playback/barge-in in a real browser, mobile/push delivery to a real device, PostgreSQL (tests run on SQLite; the Postgres exclusion constraint and advisory locks are untested), Docker, Asterisk with a real SIP trunk, the optional LLM path against a live llama-server.
- **Known issues:** Speech understanding is rule-based English only; unusual phrasing may need a human callback. Echo cancellation on phone lines relies on a raised VAD threshold while the agent is speaking; headphones are recommended for the browser demo.
