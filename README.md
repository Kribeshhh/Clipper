# Gaming Clipper

Upload recorded gameplay, review suggested highlights and export custom clips in a private single-operator studio. Project scope and contributor guidance live in [AGENTS.md](AGENTS.md).

## Start development

From this directory, use Docker with Compose v2.24.4 or later and OpenSSL. For a new checkout, generate a local environment once:

```sh
./infrastructure/scripts/init-env.sh
```

The script creates a private .env and refuses to overwrite an existing file. This workspace already has one. The example file lists the variables. Keep the generated Redis password hexadecimal. Database initialization uses a separate administrator password and provisions an application login on first volume initialization.

```sh
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
curl -fsS http://127.0.0.1:5173/api/health/live
curl -fsS http://127.0.0.1:5173/api/health/ready
```

Open [the development shell](http://127.0.0.1:5173). The server reloads edits under frontend/src and backend/app. Rebuild after dependency or build-configuration changes. Ready returns 200 when both PostgreSQL and Redis respond, or 503 with individual check results. Live checks the API process. To inspect the worker and stop the stack:

```sh
docker compose exec worker celery -A app.workers.celery inspect ping
docker compose -f compose.yaml -f compose.dev.yaml down
```

## Use the studio

Open the development shell and enter the generated OPERATOR_KEY from your private .env file. Existing Phase 0 checkouts can append a key once with the following command:

```sh
grep -q '^OPERATOR_KEY=.' .env || printf 'OPERATOR_KEY=%s\n' "$(openssl rand -hex 32)" >> .env
```

Select an MP4 or drop it onto the import panel. Upload progress switches to validation while the server probes and decodes the recording. The initial profile supports one H.264 4:2:0 video track and optional AAC audio. The defaults allow 512 MiB and 3,600 seconds, up to 120 fps and 4096 × 2160 pixels. Configure MAX_UPLOAD_BYTES, MAX_DURATION_SECONDS and MEDIA_TIMEOUT_SECONDS in .env and recreate API and worker containers after changes.

In the editor, enter start and end times in seconds or use the player's current playhead. Generate the clip, follow the render status, then play or download the finished MP4. Return to a recording to reopen its exports. Lock studio revokes the current session. Sessions expire after eight hours.

Source files and exports live in the private media-data volume. Back it up together with PostgreSQL. Keep the render worker and its embedded scheduler running for new jobs. A separate analysis worker consumes the analysis queue.

## Find highlights

Open a recording and choose **Analyze video**. The editor shows analysis progress, then lists ranked ranges and their supporting cues. Choose **Preview & edit**, adjust the timestamps as needed, and use **Generate clip**. Reopening the recording restores the results. Manual clipping stays available during analysis and after failures.

The analysis image downloads a pinned public Faster-Whisper tiny model during its first build. Inference runs locally on CPU. The model needs neither an API key nor a runtime download. If transcription fails, review the remaining signals or choose **Retry analysis**. A completed analysis can legitimately return no highlights.

Set HIGHLIGHT_WEIGHTS in .env to a JSON object using the example in .env.example. Omitted weights use defaults. Zero disables a cue. Recreate the API after changing weights and choose **Update ranking** on an analyzed recording. This reuses cached evidence.

## Export vertical clips with subtitles

In the editor, choose **Vertical 9:16**, or open a suggested highlight with **Preview & edit**. Adjust the crop slider and clip boundaries, then select **Play selection** to preview the crop and timed subtitles. **Use motion suggestion** appears when a new analysis measured consistently localized movement. **Center crop** is always available.

**Burn in subtitles** reuses the recording's transcript. If speech has not been analyzed, choose **Analyze video** or turn captions off. **Render vertical clip** saves a 720 × 1280 MP4. To approve several outputs, choose **Add to batch** after adjusting each selection, then **Render batch**. Follow each job in **Clip exports** and download completed files there.

Set `EXPORT_QUOTA_BYTES` to change the shared completed-export quota (default 5 GiB). Each render is also bounded by the per-file limit and available disk space. Recreate the worker after changing limits. Rebuild the backend images and apply migrations when upgrading from Phase 2. The Compose startup sequence applies migrations automatically.

For native captioned renders and the editing tests, put an FFmpeg build with libx264, AAC and the libass `ass` filter on PATH and install DejaVu Sans. Inspect filter availability with `ffmpeg -hide_banner -filters`. The Debian-based container build supplies these dependencies.

## Connect YouTube or Twitch

Unlock the single-operator studio and open **Live channels**. Each provider is optional. For a deployment, set `STUDIO_ORIGIN` to the exact HTTPS origin that the browser opens, without a trailing slash. Development defaults to `http://127.0.0.1:5173`. Configure the following in the private `.env` and recreate the API after changing credentials.

| Provider | App configuration | Exact callback path on the studio origin |
| --- | --- | --- |
| YouTube | Enable YouTube Data API v3, configure the consent screen and permitted test users, register a Web application and set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. | `/api/providers/youtube/callback` |
| Twitch | Register a confidential application and set `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET`. | `/api/providers/twitch/callback` |

New environments generate `OAUTH_ENCRYPTION_KEY` with the initialization script. When upgrading an existing environment that lacks this setting, append a key once from the project root:

```sh
(umask 077; printf 'OAUTH_ENCRYPTION_KEY=%s\n' "$(openssl rand -base64 32 | tr '/+' '_-')" >> .env)
```

Back up this key securely with your database. Replacing it makes existing encrypted connections unreadable. Rebuild and start the stack with the development or production command above or below to apply the migration and install the new dependency. Connect the channel in the browser and complete the provider’s consent flow. Use **Refresh broadcasts** to retrieve current status.

For YouTube, select an owned broadcast and associate an authorized upload to open it in the editor. For Twitch, **Create clip on Twitch** publishes a native clip on the connected live channel when clipping is enabled. **Browse channel clips** retrieves existing clips. Download and import are available when Twitch grants download access. Imported MP4s use the ordinary analysis and editing workflow.

### Analyze your own live encoder output

Select an active broadcast and choose **Start encoder feed**. On the encoder host, install Python 3 and FFmpeg with H.264/AAC encoding. Set `CLIPPER_INGEST_KEY` to the one-time feed credential using a private prompt, then pipe your own MPEG-TS encoder output into the publisher. For example, in Bash, with your encoder writing MPEG-TS to a named pipe:

```bash
read -rs -p 'Feed key: ' CLIPPER_INGEST_KEY
echo
export CLIPPER_INGEST_KEY
python3 infrastructure/scripts/send-live.py http://127.0.0.1:5173 FEED_UUID < /path/to/encoder.ts
unset CLIPPER_INGEST_KEY
```

Use the feed UUID shown in the studio and the HTTPS studio origin for a remote encoder. The publisher sends roughly ten-second finalized segments. The studio shows analysis progress, detected highlights, export status, edit controls and MP4 downloads. An interrupted publisher can reconnect to the same open feed with fresh input. EOF or **Stop** drains accepted segments. The defaults are `LIVE_BUFFER_SECONDS=120`, `LIVE_BUFFER_BYTES=268435456` and `LIVE_IDLE_SECONDS=180`. Lower encoder bitrate or resolution if the worker cannot keep up. Platform watch-page URLs cannot serve as encoder input.

## Deploy the personal studio over HTTPS

Install Docker Engine with Compose v2.24.4 or newer, OpenSSL and sufficient local disk for your media, backups and image builds. Allow network access during the build for packages and the pinned transcription model. The production entry point binds only to this machine’s loopback interface. All authenticated sessions belong to the same operator.

1. Generate the private `.env` as described under **Start development**. Required values are `OPERATOR_KEY`, `POSTGRES_ADMIN_PASSWORD`, `POSTGRES_PASSWORD` and `REDIS_PASSWORD`. Keep the initialized database role names and passwords for subsequent deployments. Provider settings are optional.
2. Supply a TLS certificate and key for the hostname your browser will open. Set `TLS_CERT_FILE` and `TLS_KEY_FILE` to their **absolute host paths** in `.env`. Keep both outside the repository. Nginx runs as UID 101 and must be able to read the mounted key. For local development certificates, keep the containing directory accessible only to your OS account (mode 0700) and grant read access to the mounted files. Trust your local issuing CA in the browser. Update certificates by replacing the files and recreating the frontend.
3. Set `STUDIO_ORIGIN=https://localhost:8443` when using that hostname. The browser, CSRF origin and provider callbacks must use the same origin. Use a separate environment for Vite development.

```sh
export COMPOSE_FILE=compose.yaml:compose.prod.yaml
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
curl --cacert /path/to/issuing-ca.pem https://localhost:8443/api/health/ready
```

Open **https://localhost:8443**, unlock with the operator credential and use your own uploads or authorized encoder feed. Database and Redis connections stay on the internal network. The migration container runs `alembic upgrade head` before API or workers start. The render worker runs the single scheduler. The analysis worker consumes the separate analysis queue. Both have concurrency one. Use `docker compose logs --tail=100 api worker analysis` for sanitized service events.

`RECORDING_QUOTA_BYTES` defaults to 20 GiB. At ordinary upload/import publication the API counts all video sources, including retained live windows and active segments. Live feeds also have independent buffer and highlight limits, and finished clips use `EXPORT_QUOTA_BYTES`. Size the volume for all of these plus in-flight files. Configure a host filesystem quota or dedicated volume to set a physical ceiling. Keep off-host backups encrypted.

### Back up, upgrade and restore

Finish active feeds and allow exports to complete. Use the same Compose project name and file selection as the running studio. With enough space on a protected backup volume, run:

```sh
set -eu
umask 077
backup_dir="$(mktemp -d /path/to/private-backups/clipper-XXXXXXXX)"
docker compose stop frontend api analysis worker importer
docker compose exec -T db sh -ec 'pg_dump -U postgres -Fc "$POSTGRES_DB"' > "$backup_dir/database.dump"
docker compose run -T --rm --no-deps --entrypoint tar worker -C /var/lib/clipper -czf - . > "$backup_dir/media.tgz"
docker compose start api worker analysis importer frontend
```

Protect the matching `.env`, stable OAuth encryption key, certificate materials and application version in your secret backup. A failed backup can leave writers stopped. Check both command exit statuses before accepting the snapshot, then start the stopped services. Redis sessions and queue messages are disposable. PostgreSQL contains durable jobs. For upgrades take this paired backup, rebuild with the production command above, then run `docker compose exec api alembic current` and `docker compose exec api alembic check`.

Restore to **fresh empty volumes** with a trusted backup and the original application version and credentials. Use a new Compose project name or a separate host. Stop the original frontend before starting another instance on the same port.

```sh
export COMPOSE_PROJECT_NAME=clipper-recovery
export COMPOSE_FILE=compose.yaml:compose.prod.yaml
docker compose build
docker compose up -d --wait db redis
docker compose exec -T db sh -ec 'pg_restore -U postgres --no-owner --role="$APP_DB_USER" --exit-on-error -d "$POSTGRES_DB"' < "$backup_dir/database.dump"
docker compose run -T --rm --no-deps --entrypoint tar worker -C /var/lib/clipper -xzf - < "$backup_dir/media.tgz"
docker compose up -d --wait --wait-timeout 180
```

Unlock again, check readiness and migration version, then download a known clip and compare its SHA-256 digest with the original. Keep the original volumes until the recovered studio has passed these checks.

### Troubleshooting

- **Unlock fails** — check the private operator key and exact browser origin. After 20 attempts in a minute, wait for the throttle to expire. To invalidate all browser sessions, rotate the generated key in `.env` and recreate the API. This also invalidates pending provider consent. The library stays with the same operator.
- **TLS fails** — check certificate SAN, local trust, key readability inside the frontend and that the port mapping is on loopback. Use the production overlay for HTTPS.
- **Queued jobs stay queued** — inspect `docker compose ps` and worker logs. Check database/broker readiness, free space, scratch limits, migration status and the analysis image. Restarting the workers resumes PostgreSQL-backed work. Use the editor’s retry action for a failed export.
- **Provider actions unavailable** — verify the registered callback, user scopes, channel and enabled clipping. Disconnect/reconnect when consent is revoked. YouTube provides metadata. Continuous analysis requires independent authorized footage.

## Verification

With Node 22.23 or newer, Python 3.13 and uv installed:

```sh
(cd frontend && npm ci && npm run typecheck && npm run lint && npm run format:check && npm test && npm run build)
(cd backend && uv sync --locked --group analysis && uv run --group analysis ruff check . && uv run --group analysis ruff format --check . && uv run --group analysis pytest)
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
```

Run integration tests against the running development stack. The dev API image includes the test suite and dependencies:

```sh
docker compose exec -e RUN_INTEGRATION=1 api pytest -p no:cacheprovider
docker compose exec api alembic current
docker compose exec api alembic check
```

For native integration testing install FFmpeg and FFprobe with libx264 and AAC support (and libseccomp on Linux). Use a dedicated test database. Set POSTGRES_HOST, POSTGRES_PORT, REDIS_HOST and REDIS_PORT to your test services, supply matching credentials, set MEDIA_ROOT to a private writable directory shared by API and worker, and run the following from backend:

```sh
uv run alembic upgrade head
RUN_INTEGRATION=1 uv run --group analysis pytest
```

Use `npm run format` and `uv run ruff format .` from the respective package directories to apply formatting.

For native CPU inference, set WHISPER_MODEL_DIR to the absolute path of the pinned model. From backend, provision it with:

```sh
export WHISPER_MODEL_DIR="$PWD/.venv/whisper-tiny"
uv run --group analysis python -c "import os; from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-tiny', revision='d90ca5fe260221311c53c58e660288d3deb8d356', local_dir=os.environ['WHISPER_MODEL_DIR'], allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.txt'])"
```

Use the same database, Redis and media directory settings as the render worker. On Linux run the separate analysis worker with `uv run --group analysis celery -A app.workers.celery worker -Q analysis --concurrency=1`. Compose applies the CPU and memory limits. The dev API test image omits the analysis dependency group. Run native tests with that group and the local model to exercise the complete suite.

For application security checks, run `npm audit` from frontend and the following from backend. Review every static finding against the command and path boundaries. Re-scan the built runtime images following each rebuild.

```sh
uv export --frozen --group analysis --no-emit-project --format requirements-txt -o /tmp/clipper-audit-requirements.txt
uvx pip-audit --disable-pip --no-deps -r /tmp/clipper-audit-requirements.txt
uvx bandit -r app ../infrastructure/scripts
docker compose images
# Use each built local image name from the preceding command.
trivy image --scanners vuln --severity HIGH,CRITICAL IMAGE
```

The latest personal-deployment acceptance run and outstanding OS-package scan results are recorded in the Phase 5 verification section of [AGENTS.md](AGENTS.md). The loopback deployment passed its functional checks. Review the remaining image advisories before putting private media on a new installation.

## Clip a long recording by link

Unlock the workspace and paste a YouTube video URL, a Twitch VOD URL or a Twitch clip URL into **Import recording link**. A three-hour source can stay on the provider. The studio saves its reference and your selected timestamps. Enter seconds or H:MM:SS, then choose **Save and preview selection**. If the provider blocks embedded playback, use **Open on YouTube** or **Open on Twitch**.

- **YouTube:** preview your range and copy the timestamp link. The shared watch page starts at the saved time. Creating a downloadable edited MP4 or running local AI analysis requires an independently authorized media file.
- **Twitch VOD:** connect the broadcaster through **Live channels**, select 5–60 seconds and explicitly choose **Publish selection on Twitch**. When the clip is ready, **Import clip into editor** transfers just that clip. For someone else’s channel, use the clipping options Twitch offers on its own page.
- **Existing Twitch clip:** paste the clip URL and use **Import clip into editor** when your connected broadcaster has download permission.

On upgrade, rebuild the frontend and API and apply the Alembic migration through the usual Compose startup command.

## Import an authorized direct MP4 URL

In the unlocked workspace, choose **Import from URL** beside **Upload File** and paste the direct HTTPS file URL. A signed download link works if it stays valid until the worker starts. The browser may be closed after the job is queued. Reopen this tab to see bytes received, percentage when available, validation status, cancellation and **Preview recording**. The completed source also appears in the normal recording library.

The default import limits are 8 GiB, four hours of media, one hour of download time, 30 minutes of decode validation, 8 MiB/s and a 512 MiB free-space reserve. The existing recording quota also applies. Configure the six `IMPORT_*` settings in `.env.example` according to available storage and CPU. Long-source import is separate from existing analysis and render time limits. Expired links and interrupted imports require a fresh submission. YouTube and Twitch pages belong in **Import recording link**.

Rebuild and start the stack with your usual Compose files to migrate PostgreSQL and start the new `importer` service. Native installations also need a Celery worker running `celery -A app.workers.celery worker -Q imports --concurrency=1` with the same database, broker, media root and import limits as the API. The existing scheduler dispatches these jobs.

## Generate automatic clips

Unlock the workspace, paste a permitted HTTPS MP4 link or an eligible clip link from your connected Twitch channel into **Video URL**, and select **Generate AI Clips**. The server imports the recording and renders up to ten ranked clips. Return to the workspace and select a recent run to check progress. **My Recordings** keeps the upload, import-only and manual editing tools. Each completed result shows its source range, edited duration, preview and **Download MP4**. The manual editor remains available for selecting your own moments.

Rebuild the stack using your existing Compose configuration so the migration, API, frontend, analysis worker, render worker and scheduler run the same version. Automatic generation uses the existing local Faster-Whisper model. Set `AUTO_TARGET_MIN_SECONDS` and `AUTO_TARGET_MAX_SECONDS` in the project environment to change the preferred duration range (default 20–60 seconds). A brief complete exchange can produce a shorter clip. A recording with insufficient evidence can produce zero clips. Provider player links require an authorized local recording before automatic analysis is available.

YouTube watch links and Twitch VOD pages can reuse a previously linked private recording. If there is no match, the workspace explains which authorized media link is needed. It never prompts for a local file during link submission. Upload remains available separately in My Recordings.
