# OrbiTech AI Receptionist

Multi-tenant AI voice receptionist SaaS: answers calls, answers business questions, books / reschedules / cancels appointments, and notifies the owner. Speech recognition, voice activity detection and speech synthesis run **locally on open-source models** — no paid AI APIs and no API keys.

**Stack:** Next.js 16 + React 19 + TypeScript + Tailwind (PWA) · FastAPI · SQLAlchemy/Alembic · PostgreSQL (SQLite for quick local dev) · faster-whisper or NVIDIA Parakeet (speech recognition) · Silero VAD · Kokoro-82M or Piper (voice) · Asterisk (AudioSocket) for live phone calls · Web Push (VAPID).

## Quick start (local, no Docker)

Requires Node 22+, Python 3.12+.

```bash
# 1. One-time setup
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Linux/macOS: .venv/bin/pip
.venv/Scripts/alembic upgrade head
.venv/Scripts/python seed.py                          # demo business + admin (dev only)
cd ..
npm install
cp .env.example .env                                  # the one settings file for backend, frontend and Docker

# 2. Start the backend and the frontend together (repo root)
npm run dev                                           # http://localhost:3000
```

Sign in with the seeded demo owner `demo@example.com` / `DemoOwner2026!` (demo business is labelled as demo data). Platform admin: `admin@example.com` / `PlatformAdmin2026!`. These are dev-only seed credentials; `seed.py` refuses to run when `VR_ENV=production`.

**Install speech models:** with Docker Compose the API downloads Whisper (`base.en`, ~145 MB) and Kokoro (~325 MB + voices) by itself in the background on first start (`VR_AUTO_INSTALL_MODELS`, on in `docker-compose.yml`); watch progress under *Platform admin*. Without Docker, either set `VR_AUTO_INSTALL_MODELS=true` in `.env`, or sign in as the platform admin → *Platform admin* → *Download & verify*. Files are checksum-verified and kept in the `appdata` volume, so later restarts and redeploys don't download again. Then open *AI Receptionist → Test call*.

### Choosing the speech models
Both are settings in `.env`; download the model from *Platform admin* (or with `VR_AUTO_INSTALL_MODELS`), then restart.

| Setting | Options | Notes (measured on a 4-core 2018 laptop CPU, on battery) |
|---|---|---|
| `VR_ASR_MODEL` | `base.en` (default), `tiny.en`, `small.en`, `parakeet-tdt-0.6b-v2` | Whisper always processes a 30 s window, about 1.8-2 s per reply here. Parakeet's time grows with the length of what was said (0.4-1.2 s for short replies) and it was more accurate on accented speech; it needs about 1.5 GB more RAM and ~660 MB of disk. |
| `VR_TTS_ENGINE` | `kokoro` (default), `piper` | Kokoro sounds more natural but took 2-6 s per sentence on that laptop. Piper (voices Kristin and Joe) took 0.25-0.7 s. Pick Piper on a slow CPU; a business's saved voice is swapped for one of the same gender when the engine changes. |

### Teaching it your business (website, PDF, FAQ file)
*AI Receptionist → Knowledge*: paste your website address and press **Read website**, or **Import PDF/CSV/TXT**. Text is saved as short passages under their headings and the receptionist answers only from what is saved (plus your hours, address and services): it says the one or two sentences that best answer the caller, never the whole page. Review the saved entries afterwards and delete anything you don't want said on a call.
- A website import reads the start page plus up to five same-site pages that look useful (services, FAQ, hours, insurance, contact, policies, ...), at most 120 passages. Pages whose text is drawn by JavaScript and scanned PDFs can't be read.
- Because the server fetches the address you type, it only connects to public sites on ports 80/443, re-checks every redirect, and refuses localhost, private and cloud-metadata addresses. (The address is checked just before connecting, so a hostile DNS server that changes its answer in between is not covered; run the API without access to internal networks if that matters.)
- Importing again skips passages that are already saved. Limits: PDF 5 MB / 40 pages, web page 1.5 MB.

Ready-made starting points live in `knowledge-templates/`: `doctor-office.csv` has 34 safe answers for a doctor's office (what to bring, insurance and referrals, refills, test results, records, forms, billing, privacy, emergencies and crisis lines, no medical advice). It states no prices, hours, plans or phone numbers, because only the practice knows those: add them as your own entries (or in *Business Settings*). Import it with *Import PDF/CSV/TXT*, then edit.

### Hosted services (optional)
Put `DEEPGRAM_API_KEY`, `GROQ_API_KEY` and/or `FISH_AUDIO_API_KEY` in `.env`. Each is used only when its key is set, and each falls back to the local models if the service fails (after two failures it is left alone for 30 s, so callers never wait on timeouts).

| Service | Used for | Measured | Cost control |
|---|---|---|---|
| Deepgram Nova-3 | hearing the caller | 0.35-0.8 s a turn, confidence 1.00 on clear speech; the business and service names are sent as key terms, which is what gets names right | about $0.0043 per audio minute; the $200 free credit is roughly 46,000 minutes |
| Groq (`qwen/qwen3.8-27b`) | understanding only the sentences the rules couldn't | about 220 ms and 85 tokens per question | never asked when the rules understood; one-word sentences are never sent; at most `VR_LLM_CALLS_PER_CALL` (6) per call; the prompt is short and fixed |
| Fish Audio (`VR_TTS_ENGINE=fish`) | the receptionist's voice | 1.7 s a sentence here, slower than local Piper (0.4 s) | free plan is 10,000 characters a month: each phrase is paid for once and kept on disk, and above `VR_FISH_MONTHLY_CHARS` (9,000) the local voice speaks. Off by default; with the free plan that is about 150 replies a month |

**Privacy:** with these on, the caller's audio goes to Deepgram, the words of unclear sentences to Groq, and replies to Fish Audio. Describe that in your privacy policy, and for medical or dental clients check whether a signed agreement (BAA) is needed before using them. `VR_ASR_PROVIDER=local` and `VR_LLM_PROVIDER=local` keep hearing and understanding on your machine even when keys are set.

## Settings (`.env`)

There is one settings file, `.env` in the repository root, shared by everything: the backend reads its `VR_…` lines, Next.js reads `BACKEND_URL` and `NEXT_PUBLIC_…`, and docker compose fills in its containers from it (`DB_PASSWORD` too). `.env.example` lists every setting with its default; `.env` itself is git-ignored and kept out of the Docker images. Blank values use the default, so a fresh copy works for local development as it is: the database is then a SQLite file at `backend/data/voicerep.db`. Restart after a change, and rebuild the frontend (`next build`, or `docker compose up -d --build`) after changing a `NEXT_PUBLIC_…` value.

## Using Supabase (hosted PostgreSQL, free plan)
Supabase is PostgreSQL, so the app uses it as it is. Steps:
1. Create a project at supabase.com (pick a region near your server, e.g. US East for US customers) and save the database password.
2. *Connect* → **Session pooler** → copy the string. Use the **session pooler, not the "direct connection"**: on the free plan the direct address is IPv6 only, which many networks (including most home and AWS default setups) can't reach. Don't use the *transaction* pooler (port 6543): it breaks prepared statements.
3. In `.env`, paste the address. The app repairs the usual mistakes itself (URL-encodes a password containing `@` or `$`, names the driver, adds `sslmode=require` and swaps the transaction pooler port 6543 for the session pooler 5432), so this also works as copied. Written out in full it looks like: change the start to `postgresql+psycopg://`, URL-encode special characters in the password (`@` → `%40`, `#` → `%23`), and add `?sslmode=require`:
   `VR_DATABASE_URL=postgresql+psycopg://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require`
   `VR_DB_POOL_SIZE=5` and `VR_DB_MAX_OVERFLOW=5` (the free plan allows only a few connections).
4. Create the tables once: `cd backend && .venv/Scripts/alembic upgrade head` (Docker does this at start). This also turns on row level security on every table, so Supabase's public REST API, which is open to anyone with the project's public key, can't read your data. The app connects as the `postgres` owner role and is not affected.
5. To bring your existing local data (business, knowledge base, calls, ...) over, run once: `.venv/Scripts/python copy_data.py "postgresql+psycopg://…your address…"` in `backend`. It copies only into empty tables, so running it again never duplicates anything and nothing in Supabase is deleted. (From a PostgreSQL database, use `pg_dump` and `psql` instead.)
6. Restart. From now on everything you save, including website and PDF knowledge imports, is stored in Supabase.

Know before you rely on it: the free plan has a 500 MB database and pauses a project after a week without use (check Supabase's current limits). Back up with `pg_dump` regularly. Checked on a real Supabase project (2026-11-08): the migrations run, row level security is on for every table, the no-double-booking constraint exists, and six callers booking the same slot at once gave exactly one booking. The automated tests still run on SQLite, so repeat that check after changing booking code. Keep the Supabase *service_role* key out of this app and out of git; the app doesn't need any Supabase API key, only the database address.

## Docker Compose

```bash
cp .env.example .env           # set VR_SECRET_KEY, DB_PASSWORD
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
- **NVIDIA Parakeet TDT 0.6B v2** weights are CC-BY-4.0 (credit NVIDIA wherever you attribute third-party components); `onnx-asr`: MIT.
- **`piper-tts` is GPL-3.0-or-later** (it bundles espeak-ng). The two voices are free for commercial use: Joe is CC0 and Kristin is public domain (LibriVox recordings). Other Piper voices are not: the *lessac*, *hfc_* and *ryan* voices carry non-commercial or research-only terms, so they are deliberately not offered. Same GPL advice as for espeak-ng below: hosting it as a service differs from distributing it.
- **`kokoro-onnx` phonemizes through espeak-ng (GPL-3.0)** via `phonemizer`/`espeakng-loader`. If you distribute the software, review GPL obligations (or swap in a different phonemizer/TTS). Hosting it as a service has different obligations than distributing it — get advice.
- Asterisk is GPL-2.0 and runs as a separate process.
- `soxr` (audio resampling) is LGPL-2.1-or-later, used as an unmodified library.

## Security & privacy
Argon2id passwords, server-side sessions in HttpOnly cookies, CSRF header on mutations, per-route rate limits, tenant id always taken from the server session and membership (never from the browser), tenant-scoped queries, Fernet-encrypted secrets, magic-byte audio upload validation, origin checks on WebSockets, audit events, retention purge job, call recording **off by default** (requires a consent message). Callers' speech is data only: the conversation engine can only invoke validated backend tools. In production you must provide HTTPS, `VR_ENV=production`, a strong `VR_SECRET_KEY`, `VR_COOKIE_SECURE=true`, PostgreSQL backups (`pg_dump` nightly; restore with `psql`) and monitoring (`/api/health`). You are responsible for call-recording, privacy and AI-disclosure law compliance; a checkbox does not make you compliant.

## Tests
```bash
cd backend && .venv/Scripts/python -m pytest -q     # 203 tests (the real-audio ones are skipped until the speech models are installed)
npx tsc --noEmit && npx eslint . && npm run build
```

### Test report (last run in development environment)
- Backend: **203 passed** (one full run on Python 3.13 with Parakeet, Piper and Kokoro installed; earlier runs without the speech packages skipped the real-audio tests). Covers phone call start, audio and hang-up behaviour, auth/CSRF/rate limit, tenant isolation, staff role, hours validation, availability (hours, breaks, buffers, holidays, notice, staff calendars), concurrent double-booking (6 threads → 1 booking), reschedule/cancel/status transitions, policy notice, contact de-duplication, DST/time-zone handling, reminder uniqueness and exactly-once delivery across 4 workers, upload validation, telephony secrets/dialplan auth/AudioSocket failures, LLM output validation, dialog safety (emergency, injection, medical advice, no invented prices), the 10-step booking scenario over WebSocket, migrations, and a **real-audio** test (synthesized speech → Silero → Whisper → dialog → Kokoro) when models are installed.
- Frontend: `tsc --noEmit`, `eslint` and `next build` pass. UI was exercised manually in a browser pane: login, overview, and a full typed booking conversation that persisted an appointment, notification and reminder.
- **Not verified:** real microphone capture (blocked in the test browser — use *Test microphone* in the UI), audible playback/barge-in in a real browser, mobile/push delivery to a real device, PostgreSQL (tests run on SQLite; the Postgres exclusion constraint and advisory locks are untested), Docker, Asterisk with a real SIP trunk, the optional LLM path against a live llama-server.
- **Known issues:** Speech understanding is rule-based English only; unusual phrasing may need a human callback. Echo cancellation on phone lines relies on a raised VAD threshold while the agent is speaking; headphones are recommended for the browser demo.
