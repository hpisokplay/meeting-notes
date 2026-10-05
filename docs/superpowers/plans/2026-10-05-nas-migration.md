# DD會議紀錄 NAS 版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move DD會議紀錄 from a browser-only Gemini PWA + GitHub sync to a NAS-hosted app: FastAPI server on the Synology, transcription on the user's PC GPU (faster-whisper + pyannote), all text AI via `claude -p` (Claude Max), Telegram access through the existing tgbridge bot.

**Architecture:** One Python server (`server/`) owns the data folder, REST API, chunked uploads, a file-based job queue, an AI job thread calling `claude -p`, a NAS-CPU fallback transcriber and TG notifications; it also serves the existing static PWA. A separate Windows worker (`worker/`) polls the server, transcribes on GPU and posts segments back. The frontend keeps its UI and pure helpers but swaps Gemini/Groq/GitHub/IndexedDB-as-truth for `js/api.js`.

**Tech Stack:** Python 3.11+ (FastAPI, uvicorn, python-docx, pytest, httpx TestClient), faster-whisper 1.2.x, pyannote.audio 4.0.x, OpenCC, ffmpeg; vanilla ES modules + vitest/jsdom on the frontend; Docker on Synology DSM 7.3; Tailscale serve.

**Spec:** `docs/superpowers/specs/2026-10-05-nas-migration-design.md` (read it first; section numbers below refer to it).

## Global Constraints

- Server listens on port **8778**; public URL `https://ddys.tail95e8ec.ts.net:8443/`.
- Data root is env `MEETINGS_DATA` (container `/data`); every write is atomic (`.tmp` + `os.replace`).
- Nothing is ever hard-deleted: deleting a meeting moves it to `trash/`; import merges and never drops existing meetings.
- Meeting JSON keeps every legacy field untouched (`transcript[].t` may be number, "MM:SS" string, or absent with `fi`).
- No Gemini, Groq or GitHub calls anywhere in the shipped app.
- Upload chunk size 8 MiB; must handle ≥ 1000 MB files and ≥ 8 h audio.
- Worker auth: header `X-Worker-Key` must equal the contents of env `MEETINGS_WORKER_KEY_FILE` (default `/run/meetings/worker.key`).
- Claude call: `claude -p --model <m> --tools "" --output-format json --no-session-persistence --max-turns 3 --json-schema <schema> --system-prompt <prompt>`, transcript on stdin, read `structured_output`. Clear any inherited `CLAUDE*`/`ANTHROPIC*` env vars except `CLAUDE_CODE_OAUTH_TOKEN`/`CLAUDE_CONFIG_DIR` before spawning.
- All user-visible text is Traditional Chinese (Taiwan).
- Frontend `APP_VERSION` becomes `v100`; service-worker cache name `meeting-app-v100`.
- Speakers from the GPU worker are named `說話者 1`, `說話者 2`… in order of first appearance; NAS-CPU fallback uses `說話者`.

## Review Focus

1. **Phone loses connection mid-upload of a 1 GB file** → reopening the page resumes from the last received chunk, not from zero (Task 2 test `test_upload_resume_reports_received_chunks`; Task 7 test `resumes upload from server-reported chunks`).
2. **Worker crashes after claiming a job** → job returns to queue after 10 min without progress, and is not lost (Task 3 test `test_stale_running_job_requeued`).
3. **Claude hits Max usage limit** → job shows a clear waiting message and retries later instead of failing the meeting permanently (Task 4 test `test_rate_limited_job_is_retried_later`).
4. **Legacy meeting with string timestamps / no transcript `t`** → renders, exports and imports without crashing (Task 1 test `test_render_md_legacy_timestamps`; Task 5 test `test_import_legacy_backup_keeps_everything`).
5. **Whisper hallucination lines in silence** ("中文字幕由 Amara.org 社群提供", prompt echo) → removed before saving (Task 6 test `test_filter_drops_known_hallucinations`).

---

## File Structure

```
server/
  app.py            FastAPI app factory create_app(data_dir, *, claude_cmd=None, start_threads=True); static mount; router includes
  storage.py        Store class: paths, atomic json io, meetings CRUD, trash, groups, index.json, settings, per-id locks
  render.py         meeting_to_markdown(m) and meeting_to_docx(m, path)
  uploads.py        chunked upload endpoints + create meeting + audio Range serving + inbox scan
  jobs.py           JobQueue (file-based queue/running/done/failed), worker endpoints, stale requeue, pipeline hooks
  ai.py             Claude runner + action handlers (summary/enhance/notes/notes_enhance/ask/terms/translate)
  prompts.py        prompts + JSON schemas ported from js/gemini.js
  fallback.py       NAS CPU transcriber (faster-whisper small int8, ffmpeg→wav→numpy)
  notify.py         Telegram Bot API notify(text)
  importer.py       merge legacy backup JSON into Store
  backup.py         daily metadata backup (tar.gz of meetings/, groups.json, settings.json) keep 14
  main.py           entry: uvicorn run, .restart watcher, background threads
  requirements.txt
  tests/            pytest suites per module
worker/
  worker.py         poll loop; process_job(job) → segments
  pipeline.py       pure functions: align_words(words, turns), build_segments(...), filter_hallucinations(segs), name_speakers(segs)
  asr.py / diar.py  GPU wrappers (lazy imports)
  install.ps1       create venv, copy models, write config, register scheduled task
  tests/
js/
  api.js            NEW: REST client (meetings, groups, uploads, ai jobs, status, settings, import/export)
  app.js            MODIFY: replace gemini/groq/sync/store calls with api.js; new upload + progress + settings + player
  store.js          MODIFY: becomes a thin IndexedDB read cache of server data
  (delete) gemini.js groq.js sync.js usage.js settings.js audio.js mp4.js and their tests
deploy/nas/         Dockerfile compose.yaml claude-wrapper.sh install.sh update_app.sh
bot-skill/meetings-db/SKILL.md
tools/import_legacy.py   CLI: POST a legacy backup file to /api/import
```

---

### Task 1: Server foundation — storage, render, meetings/groups/status API, static files

**Files:**
- Create: `server/storage.py`, `server/render.py`, `server/app.py`, `server/main.py`, `server/requirements.txt`, `server/tests/conftest.py`, `server/tests/test_storage.py`, `server/tests/test_api_meetings.py`

**Interfaces:**
- Produces:
  - `Store(root: Path)`: `.list_meetings() -> list[dict]` (no transcript; fields id,title,createdAt,updatedAt,group,durationSec,status,progress), `.get(id) -> dict|None`, `.save(m: dict) -> dict` (sets updatedAt=now ms, writes json, regenerates `meetings/<id>.md`, `exports/<id>.docx`, `index.json`), `.update(id, fn: Callable[[dict], None]) -> dict` (locked read-modify-write), `.trash(id)`, `.get_groups()/.set_groups(list)`, `.get_settings()/.set_settings(dict)` (defaults `{"claudeModel":"sonnet","fallbackEnabled":True,"fallbackMinutes":20,"tgNotify":True}`), `.new_id() -> str` (ms timestamp string + 4 random hex), `.path(*parts) -> Path`.
  - `meeting_to_markdown(m) -> str`, `meeting_to_docx(m, path: Path) -> None`.
  - `create_app(data_dir: Path, *, claude_cmd: list[str]|None=None, start_threads: bool=True) -> FastAPI`; `app.state.store`.
- [ ] Step 1: tests — `test_save_get_roundtrip`, `test_atomic_write_leaves_no_tmp`, `test_trash_moves_file_never_deletes`, `test_index_json_lists_meetings`, `test_render_md_legacy_timestamps` (transcript items `{"speaker":"A","text":"x","t":"01:02"}` and `{"speaker":"B","text":"y","fi":1}` render without error and include both texts), `test_api_list_get_put_delete`, `test_api_groups_roundtrip`, `test_status_shape` (`version`, `worker.online` bool, `queue` int, `claudeModel`, `diskFreeGB`), `test_static_index_served` (GET `/` returns index.html from configured static dir env `MEETINGS_STATIC`, default repo root).
- [ ] Step 2: run `python -m pytest server/tests -q` → fail.
- [ ] Step 3: implement. Markdown layout: `# 標題`, `日期：YYYY-MM-DD HH:MM（台北）`, `分類：`, `長度：`, `## 待辦` `## 重點` `## Q&A` (from summary), `## 逐字稿` lines `[HH:MM:SS] 說話者: 文字` (time omitted if not numeric). Docx: python-docx, same sections.
- [ ] Step 4: tests pass.
- [ ] Step 5: commit `feat(server): storage, render and meetings API`.

### Task 2: Chunked uploads, meeting creation, audio serving, inbox

**Files:** Create `server/uploads.py`, `server/tests/test_uploads.py`; Modify `server/app.py` (include router).

**Interfaces:**
- Consumes: `Store`, `JobQueue.enqueue(kind, meeting_id, payload) -> str` (Task 3; until Task 3 lands, use a stub that writes `jobs/queue/<jobId>.json`; Task 3 replaces it — keep the call site `app.state.jobs.enqueue("transcribe", mid, {})`).
- Produces: endpoints in spec §5 (`POST /api/uploads`, `GET /api/uploads/{uid}`, `PUT /api/uploads/{uid}/{index}`, `POST /api/meetings`, `GET /api/meetings/{id}/audio/{n}`, `POST /api/meetings/{id}/retranscribe`); `scan_inbox(store, jobs)` callable.
- [ ] Step 1: tests — `test_upload_resume_reports_received_chunks` (create upload size=20 MiB, PUT chunk 0 and 2, GET returns `received:[0,2]`, `chunkSize:8388608`, `chunks:3`), `test_complete_assembles_and_creates_meeting` (assembled file byte-equal; meeting `status=="queued"`, `audio[0].size` correct; a transcribe job exists in `jobs/queue`), `test_multi_file_order_preserved`, `test_incomplete_upload_rejected_409`, `test_audio_range_request` (Range: bytes=0-9 → 206, 10 bytes, Content-Range), `test_inbox_file_becomes_meeting` (drop `inbox/a.m4a` → `scan_inbox` creates meeting titled from filename, moves file to `audio/<id>/`), `test_chunk_index_out_of_range_400`.
- [ ] Step 2: fail. Step 3: implement — chunks stored as `uploads/<uid>/<index>.part`; meta `uploads/<uid>/meta.json {name,size,mime,chunks,created}`; assembly streams parts into `audio/<id>/<n>-<safe name>` then removes upload dir; durationSec via `ffprobe` if available else null. Audio route streams file with Range support (`StreamingResponse`, 1 MiB reads).
- [ ] Step 4: pass. Step 5: commit `feat(server): resumable chunked uploads and audio serving`.

### Task 3: Job queue, worker API, pipeline

**Files:** Create `server/jobs.py`, `server/tests/test_jobs.py`; Modify `server/app.py`.

**Interfaces:**
- Produces: `JobQueue(store)`: `.enqueue(kind, meeting_id, payload) -> jobId`, `.claim(kinds: list[str], owner: str) -> dict|None` (oldest queued → running, sets `claimedAt`,`owner`,`heartbeat`), `.progress(jobId, stage, pct, msg)`, `.finish(jobId, result=None)`, `.fail(jobId, error, retry_at: float|None=None)` (retry_at → back to queue with `notBefore`), `.get(jobId)`, `.cancel(jobId)`, `.requeue_stale(timeout_s=600)`, `.queued_age(jobId) -> float`. Job JSON: `{id, kind, meeting, payload, state, created, claimedAt, owner, heartbeat, progress:{stage,pct,msg}, error, retries, notBefore}`. `worker_last_seen` stored in `jobs/worker.json`.
- Worker endpoints per spec §5 with `X-Worker-Key` check (401 otherwise). `GET /api/worker/next` returns `{job, audio:[{url, name, size}]}` or `204`. Result handler: writes `transcript` (segments `{speaker,text,t}`), `durationSec`, `progress.engine`, sets `status="summarizing"`, enqueues `ai` job `{action:"summary"}`; meeting `status` set by AI completion to `done`.
- `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel` (cancel of a transcribe job sets meeting `status="failed"`, `error="已取消"`).
- [ ] Step 1: tests — `test_claim_oldest_first`, `test_worker_key_required`, `test_next_returns_204_when_empty_and_updates_heartbeat`, `test_result_writes_transcript_and_enqueues_summary`, `test_stale_running_job_requeued` (claimedAt/heartbeat 11 min ago → back in queue, retries+1), `test_fail_with_retry_at_hidden_until_time`, `test_cancel_marks_meeting`.
- [ ] Steps 2-5 as usual; commit `feat(server): file job queue and worker API`.

### Task 4: Claude runner and AI actions

**Files:** Create `server/prompts.py`, `server/ai.py`, `server/tests/test_ai.py`, `server/tests/fake_claude.py`; Modify `server/app.py`.

**Interfaces:**
- Consumes: `Store.update`, `JobQueue` (claim kinds `["ai"]` from an internal thread, owner `"nas-ai"`).
- Produces: `run_claude(system: str, user: str, schema: dict, model: str, cmd: list[str]) -> dict` raising `ClaudeLimitError` (stderr/result mentions usage limit / rate limit / 429 / "limit reached") or `ClaudeError`; `handle_ai_job(store, job, cmd)`; `POST /api/meetings/{id}/ai` → `{jobId}`; `AIThread(store, jobs, cmd).start()`.
- Port from `js/gemini.js`: `SUMMARY_PROMPT` and its schema (actionItems/mainPoints/qa), the enhance prompts per section, notes prompt + schema (outline/concepts/tables/figures/quiz), notes enhance per section, ask prompt (answer in 繁中 citing transcript), term-scan prompt + schema (items with t, type 人名/公司/產品/地名/國家/術語, alts), translate prompt per lang. Read the JS to copy wording exactly; transcript serialised as `[HH:MM:SS] 說話者: 文字` lines.
- Write-back rules: enhance/notes_enhance copy previous section into `alt[kind][section]` before overwrite (match `backupSection` in `js/app.js:132`); summary on a meeting that already has one backs it up to `alt.summary`; ask appends `{q,a,at}` to `chat`; terms sets `terms.items` (keep existing `applied`/`draft` for same `t`), `terms.scannedAt`; translate sets `translations[lang]` (same shape the frontend renders — check `js/app.js:1907-1960`); summary job from pipeline sets meeting `status="done"`.
- Limit handling: `ClaudeLimitError` → `jobs.fail(id, "Claude 用量已達上限，30 分鐘後自動重試", retry_at=now+1800)` up to 6 retries, then permanent fail and meeting `error`.
- Tests use `fake_claude.py` (a script printing a canned `{"structured_output":...}` chosen by env `FAKE_CLAUDE_MODE`): `test_summary_written_and_status_done`, `test_enhance_backs_up_old_section`, `test_ask_appends_chat`, `test_terms_preserve_applied_flags`, `test_rate_limited_job_is_retried_later`, `test_env_scrubbed_for_child` (fake prints its env; assert no `CLAUDECODE`).
- Commit `feat(server): Claude Max AI actions`.

### Task 5: Import/export, settings, backup, notify, NAS fallback, main entry

**Files:** Create `server/importer.py`, `server/backup.py`, `server/notify.py`, `server/fallback.py`, tests `server/tests/test_import.py`, `test_fallback.py`, `test_notify.py`; Modify `server/app.py`, `server/main.py`; Create `tools/import_legacy.py`.

**Interfaces:**
- `merge_legacy(store, data: dict) -> dict(report)`: for each legacy meeting: absent → add (status `done`); present → keep the one with newer `editedAt||updatedAt`, union `chat` by `at`, keep `notes`/`alt`/`translations` if either has; groups merged by id; legacy `deleted` ids are **ignored** (never delete). Returns `{added, updated, kept, groups}`.
- `GET /api/export` returns `{meetings:[...full...], groups, deleted:[], exportedAt}` (legacy-compatible).
- `notify(text)`: reads `/run/tg/token`, `/run/tg/allowed_ids` (first id), POSTs `sendMessage`; silently no-op if files missing; honours `settings.tgNotify`.
- `FallbackThread`: every 60 s, if `settings.fallbackEnabled` and a transcribe job queued longer than `fallbackMinutes` and worker not seen in last 2 min → claim with owner `"nas-cpu"`, ffmpeg to 16 kHz wav, faster-whisper `small` int8 (`cpu_threads=4`), numpy input (no PyAV decode), OpenCC s2twp, segments speaker `說話者`, then same result path as worker. Imports lazy so tests run without faster-whisper (test with monkeypatched transcriber).
- `main.py`: `uvicorn.run(create_app(...), host, port=8778)`; thread watching `/data/.restart` (delete it and `os._exit(0)`); start AI, fallback, stale-requeue (every 60 s), inbox scan (every 30 s), daily backup 03:40.
- Tests: `test_import_legacy_backup_keeps_everything` (use a trimmed copy of a real legacy record with string `t`; existing local meeting not in import stays), `test_import_ignores_deleted_list`, `test_export_roundtrip`, `test_fallback_claims_only_after_wait`, `test_notify_noop_without_token`.
- Commit `feat(server): import/export, fallback transcriber, notify, entrypoint`.

### Task 6: PC GPU worker

**Files:** Create `worker/pipeline.py`, `worker/asr.py`, `worker/diar.py`, `worker/worker.py`, `worker/install.ps1`, `worker/requirements.txt`, `worker/tests/test_pipeline.py`, `worker/tests/test_worker_loop.py`.

**Interfaces:**
- Consumes: server worker API (spec §5): `GET /api/worker/next` → `{job:{id,meeting,...}, audio:[{url,name,size}]}`; progress/result/fail POSTs.
- Produces (pure, unit-tested): `align_words(words: list[tuple[float,float,str]], turns: list[dict]) -> list[tuple[float,float,str,str]]` (speaker by max overlap, else nearest turn), `build_segments(aligned, gap=2.0, max_chars=60) -> list[dict{speaker,text,t}]` (split at sentence punctuation once over max_chars), `filter_hallucinations(segs, prompt) -> list` (drop segments containing any of: `Amara`, `字幕志願者`, `字幕由`, `中文字幕`, `請不吝點贊`, `訂閱`, `謝謝觀看`, `謝謝收看`, the prompt text or its prefix ≥ 6 chars; and collapse ≥ 3 consecutive identical texts to one), `name_speakers(segs) -> list` (`SPEAKER_xx` → `說話者 N` by first appearance), `offset_parts(parts: list[list[dict]], durations: list[float]) -> list[dict]`.
- GPU flow per spec §6.3. ASR: `BatchedInferencePipeline(WhisperModel("large-v3","cuda","float16")).transcribe(np_audio, batch_size=8, language="zh", vad_filter=True, beam_size=5, condition_on_previous_text=False, word_timestamps=True, initial_prompt=<short neutral prompt>)`; audio loaded via ffmpeg → 16 kHz s16le wav → numpy (no PyAV). Diar: `Pipeline.from_pretrained(<models>/pyannote-c1).to(cuda)` with waveform dict. Free whisper (`del model; torch.cuda.empty_cache()` / gc) before pyannote. OpenCC s2twp on text.
- Progress posts: download %, `asr` %, `diar`, `upload`. Download uses Range resume into `%LOCALAPPDATA%\MeetingWorker\cache\<jobId>\`.
- Config `%LOCALAPPDATA%\MeetingWorker\config.json` `{server, key, poll:10}`; log rotating `worker.log` 5×5 MB; HTTP timeouts 60 s; any exception → `fail` with message, continue loop.
- `install.ps1`: create venv (python 3.12), pip install `faster-whisper==1.2.1 opencc-python-reimplemented requests` + torch cu128 + `pyannote.audio==4.0.7`, copy `pyannote-c1` folder from a given path, write config, register scheduled task `MeetingWorker` (at logon, `pythonw worker.py`, restart on failure).
- Tests: `test_align_words_overlap_and_nearest`, `test_build_segments_merges_same_speaker_gap`, `test_build_segments_splits_long`, `test_filter_drops_known_hallucinations`, `test_filter_collapses_repeats`, `test_name_speakers_order`, `test_offset_parts`, `test_loop_posts_fail_on_exception` (fake HTTP session).
- Commit `feat(worker): PC GPU transcription worker`.

### Task 7: Frontend on the NAS API

**Files:** Create `js/api.js`, `tests/api.test.js`; Modify `js/app.js`, `js/store.js`, `js/groups.js`, `index.html`, `sw.js`, `manifest.webmanifest`, `README.md`; Delete `js/gemini.js`, `js/groq.js`, `js/sync.js`, `js/usage.js`, `js/settings.js`, `js/audio.js`, `js/mp4.js` and their tests.

**Interfaces:**
- `api.js` (base = same origin; all functions throw `Error` with server `detail` text): `listMeetings()`, `getMeeting(id)`, `saveMeeting(m)`, `deleteMeeting(id)`, `getGroups()`, `saveGroups(g)`, `getStatus()`, `getSettings()`, `saveSettings(s)`, `startUpload(file) -> {uploadId, chunkSize, received}` (reuses an uploadId stored in localStorage under `upload:<name>:<size>:<lastModified>` if server still has it), `uploadFile(file, onProgress, signal)` (PUT missing chunks, 3 retries each with backoff, returns uploadId), `createMeeting({title, group, uploads})`, `runAI(id, action, params) -> jobId`, `getJob(jobId)`, `waitJob(jobId, onProgress, signal)` (poll 2 s), `cancelJob(jobId)`, `retranscribe(id)`, `audioUrl(id, n)`, `importBackup(obj)`, `exportAllUrl()`, `docxUrl(id)`.
- `store.js`: keep IndexedDB but only as cache: `cacheList(list)`, `cachedList()`, `cacheMeeting(m)`, `cachedMeeting(id)`; app shows cached data instantly then refreshes from server; offline banner when fetch fails.
- `app.js` changes (keep all pure exported helpers and their tests): upload screen (multi-file, name sort, per-file + total progress, wakeLock during upload, resume); progress screen polls `getMeeting` every 3 s showing `status`/`progress` (stage text map: queued 排隊中（等電腦轉錄器）, transcribing 轉錄中, summarizing 摘要中, done, failed) plus Stop (= cancel job) and 取消並刪除; list shows status badge; detail: every AI button calls `runAI` then `waitJob` then reloads meeting; enhance/swap/merge/edit/delete-item/rename-speaker/term apply still mutate locally then `saveMeeting`; audio player (`<audio controls preload="none">` per part) and clicking a timestamp seeks the right part; 重新轉錄 button when `audio.length`; settings page: NAS status (轉錄器 線上/離線, 佇列, Claude 型號 select sonnet/opus/haiku, NAS 備援 toggle + 等待分鐘, TG 通知 toggle), 匯入備份 (file → `importBackup`), 匯出備份 (link), 強制更新. Remove key/model/GitHub UI.
- Tests: `tests/api.test.js` (fetch mocked): `resumes upload from server-reported chunks` (server says received [0,1] of 3 → only chunk 2 PUT), `retries a failed chunk then succeeds`, `waitJob resolves on done and rejects on failed with server error text`; update `tests/app.dom.test.js` to mock api instead of gemini; delete obsolete test files; keep format/search/export/docx/groups tests green.
- Run `npx vitest run` → all pass. Commit `feat(web): frontend on NAS API`.

### Task 8: Deploy, data migration, Telegram skill

**Files:** Create `deploy/nas/{Dockerfile,compose.yaml,claude-wrapper.sh,install.sh,update_app.sh,requirements.txt}`, `bot-skill/meetings-db/SKILL.md`.

- Dockerfile: `FROM node:22-bookworm-slim`; apt `python3 python3-pip ffmpeg ca-certificates fonts-noto-cjk procps`; pip `-r requirements.txt` (server deps + `faster-whisper==1.2.1 opencc-python-reimplemented`); `npm i -g @anthropic-ai/claude-code@2.1.280`, wrapper as `/usr/local/bin/claude`; `ENV MEETINGS_DATA=/data MEETINGS_STATIC=/app/web HOME=/claude-home HF_HOME=/data/.hf`; `CMD ["python3","-m","server.main"]` with `WORKDIR /app`.
- compose: container `meetings`, `127.0.0.1:8778:8778`, `mem_limit: 2500m`, dns, volumes per spec §3.1, `restart: unless-stopped`.
- `update_app.sh`: tar `server/` + web files (index.html, css, js, icons, sw.js, manifest) → `ssh nas` into `/volume1/docker/meetings/app.new`, swap (rename old to `app.old-<ts>`), touch `/volume1/projects/meetings/.restart`.
- `install.sh` (run once by user with sudo): mkdir all dirs (Synology won't create bind sources), generate `worker.key` if missing (mode 600), `docker compose up -d --build`, `tailscale serve --bg --https=8443 http://127.0.0.1:8778`, print status.
- Migration: copy legacy `meetings.json` to NAS `/volume1/projects/meetings/import/legacy-meeting-notes-data.json`; server imports any `import/*.json` at start once (move to `import/done/`). Add that to Task 5's main? → implement here as `importer.import_dir(store)` called by main at startup (add test `test_import_dir_moves_done`).
- SKILL.md per spec §10 (query, send files via `_outbox`, inbox for audio ≤ 20 MB, never delete, answer rules like anchors-db).
- Verification: shellcheck-free review; `python -m pytest server/tests -q` still green.
- Commit `feat(deploy): NAS docker, install script, TG skill`.

### Task 9: End-to-end verification

- [ ] Local: run server with `MEETINGS_DATA=<scratch>/e2e` on PC, real `claude`, real worker pointed at `http://127.0.0.1:8778`; upload 元澄 recording through the browser pane; confirm transcript with speakers, summary, each AI action, docx export, audio seek.
- [ ] Long input: build an 8 h file by concatenating recordings with ffmpeg (> 1000 MB as WAV or 8 h m4a), upload, confirm resume after killing the page mid-upload, confirm worker finishes and memory stays OK.
- [ ] NAS: deploy via `update_app.sh` after the user runs `install.sh`; import legacy data; open from phone viewport; TG skill installed and backup kept.
- [ ] Record actual numbers in the morning report.
