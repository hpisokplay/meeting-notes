# Task C report: pipeline, endpoints, settings

Status: DONE. Commit `cca8a6f` on `quality-v2`, on top of B's fix round `75e8951`. Server suite: **206 passed**. New tests are in `server/tests/test_reprocess.py` (20 tests).

## What was built
- **`server/ai.py`**
  - New action `reprocess`. Steps run in this order: correction → mode (only if absent) → grounded summary (mode-aware) → notes (only if the meeting already has notes) → SLIPS → verify.
  - All writes happen once, at the end, under the meeting lock:
    - `transcriptRaw` is saved if absent.
    - Old summary/notes go into `alt` with flat keys.
    - Translations are cleared.
  - If the transcript was edited during the run, the job fails with 「逐字稿在整理期間被修改…」. It does not overwrite the edit.
  - If CORRECTION or SLIPS fails permanently, that step is skipped. Limit, transient and abort errors still retry or cancel the job.
- **Existing actions**
  - `summary`: now grounded and mode-aware (Items with src), with verify. It does no correction.
  - `enhance`: uses the src prompts and polish. Covered raw items' src are merged, at most 3.
  - `notes` and `notes_enhance`: use the src prompts.
  - All of these re-verify, after re-anchoring src (`verify.relocate_src`, via hasattr).
  - `ask` and `translate` accept Items and the lecture keys.
- **Progress**
  - The job stage and `meeting.progress.stage` are one of correction / mode / summary / notes / slips / verify.
  - The messages are exactly 「名詞校正中」「判斷講座／會議」「整理摘要中」「整理筆記中」「檢查口誤中」「驗證中」.
  - `meeting.progress.action` is set to the running action. `GET /api/jobs/{id}` also returns `action`.
- **Pipeline state**
  - When an ai job is a pipeline job (`aiJobId` == job and status "summarizing"), it ends with status done.
  - The default model is opus.
- **`server/review.py` (new)**
  - Correction merge and apply. High confidence is applied. Medium and low become pending.
  - Pairs that are already known keep their state, so a rejected pair or a pair reset by restore is never auto-reapplied.
  - Digit-changing pairs are never auto-applied.
  - Replacement uses Latin word boundaries (CIS will not match CISCO).
  - Slip ReviewItems are built here, along with the verify wrapper and the review actions.
  - Endpoints: `POST /review/{rid}`, `/mode`, `/restore-raw`, plus `start_reprocess`, which sets `aiJobId`, status "summarizing" and `progress.action`.
  - A second reprocess while one is running, or while transcribing, returns 409.
- **Review actions**
  - On a summary/notes item:
    - **edit** replaces the text by itemId and keeps the Item id. For legacy strings, the sibling items' itemId is re-pointed.
    - **delete** moves the item to `alt["deleted:<section>"]`. Pending siblings of the same item are marked deleted.
    - If the target item is missing, the review item is treated as resolved and the endpoint returns 200.
  - On a **term** item:
    - **confirm** applies the suggestion to the transcript (raw saved first).
    - **edit** applies right=text.
    - **delete** rejects the correction.
  - On a **slip** item, only the review item's state changes.
  - Every action returns the full updated meeting, and `updatedAt` is bumped.
- **`/mode` and `/restore-raw`**
  - `/mode` sets the mode, then reprocesses with `{correction: false, slips: false}`, because the transcript did not change.
  - `/restore-raw` sets transcript = raw and resets every correction to pending. It then reprocesses with `{correction: false}`, so nothing is re-applied automatically.
- **`server/jobs.py`**
  - A transcription result now enqueues `reprocess` instead of `summary`.
  - A new transcription drops the old `transcriptRaw` and `corrections`.
- **`server/storage.py`**
  - `DEFAULT_GLOSSARY` has 70 terms.
  - `settings.glossary` is stored as a string. It is kept when absent from a PUT, and lists are joined.
  - `claudeModel` defaults to opus. A one-time migration changes sonnet to opus (`modelMigrated`). After the migration, an explicit choice of sonnet is kept.
- **Transcribe payload**
  - `uploads.py` (create, retranscribe and inbox) adds `{glossary: [...]}` to the transcribe job payload.
  - The NAS fallback passes the glossary as a Whisper `initial_prompt` of at most 200 chars.
- **`server/render.py`**: the md/docx output renders Items and the lecture sections.
- **`server/app.py`**: `transcriptRaw`, `corrections`, `review` and `mode` are server-owned on PUT.
- **Tests**
  - `fake_claude.py` was extended for MODE, SUMMARY_MEETING/LECTURE, enhance/notes with src, and CORRECTION/SLIPS via env vars.
  - Existing tests that asserted the old behaviour were updated: string-only summary/enhance/notes output, the sonnet default, and the `summary` pipeline payload.
- I did not edit `verify.py`, `items.py` or `prompts.py`.

## Real smoke test
Meeting: 會計師第一場. It ran on a copy in a scratch data dir with the real `claude` CLI and opus. I ran one full reprocess with no notes regeneration skipped.
- **Time:** 950 s total. Per call: correction 121 s, mode 6 s, lecture summary 379 s, notes 409 s, slips 34 s. Verify was negligible.
- **Mode:** lecture.
- **Corrections:** 104 pairs. 70 were applied, making 174 replacements. 34 are pending.
- **Summary sections:** conclusions 7, advice 9, risks 8, cases 19, mainPoints 25, qa 0. Every Item has src.
- **Notes:** outline 25, concepts 14, tables 7, figures 55, quiz 17.
- **Review items:** 74 pending (term 34, number 17, name 15, slip 8).
- **Audit issues:**
  - 許崑源: 0 occurrences.
  - 鼓勵所得: 0 in the transcript (股利所得 ×18), so it is fixed at the source.
  - 5000元: gone. The case now reads 「講者原話為『只要繳五千塊』，依上下文（稅額減半）疑為五千萬」. It is also flagged as a slip, along with 133天, 48 vs 40, 40億 vs 50億, and others.

## Concerns
1. **Auto-apply is aggressive.** 70 high-confidence pairs were applied, for example 簽出→遷出, 結會→結匯, 營運分配→盈餘分配 and 李專→理專. They are all restorable, and listed in corrections, but nobody reviews them. Worth an audit pass in Task E.
2. **Review list is long.** 74 pending items is a lot of work for the user. Many of the 17 number flags are on notes outline sections that use the "section time range" window, for example 750, 1000 and 300. They look like false positives, where the transcript probably says the number in words or outside the span. This is for Task B's verify to tune.
3. **Time per run.** About 16 minutes for an 89-minute lecture with notes, using sequential opus calls.
4. **Notifications.** A manual reprocess, mode switch or restore now uses the pipeline state, so the Telegram 「整理完成」 notification also fires for these runs.

## Fix round 1

All 8 items are fixed. The server suite now has **218 tests passing**: the 206 from before, plus 11 in `test_reprocess_fix1.py`, plus the updated `/mode` test. I did not touch verify.py, items.py or prompts.py. This round only uses `verify.extract_numbers`.

1. **Safe auto-correction** (`review.py`).
   - A high-confidence pair is applied automatically only if all of these hold. If any fails, it becomes pending with `demoted: true`.
     - (a) Neither `wrong` nor `right` is contained in the other.
     - (b) `wrong` has at least 3 CJK characters, or is a Latin token. Latin tokens are matched with word boundaries.
     - (c) `extract_numbers(wrong) == extract_numbers(right)`, so no number ever changes automatically.
     - (d) The model reported ts, and replacement only touches segments within ±15 s of them.
     - (e) Matches inside those windows are no more than max(3, 2×len(ts)).
   - Two more guards apply to every replacement:
     - A match that lies inside an existing occurrence of `right` is never replaced.
     - A user-confirmed or edited term uses the same windows, built from the pending pairs' ts. Only when no ts exist does it replace everywhere.
   - Pending pairs now store the model's ts.
   - Tests cover:
     - 股利→股利所得: demoted, and no doubling even on a manual apply.
     - 證與→贈與: demoted, and a confirm leaves 保證與否 untouched.
     - 繳五千塊→繳五千萬 and 一百八十三天→一百三十三天: demoted.
     - A match outside ±15 s is left alone.
     - Too many matches, or no ts: demoted.
     - CIS→CRS leaves CISCO alone.
2. **restore-raw** drops the review items of kind "term", so the next verify rebuilds them as pending. The ts on each correction are kept. Tested: confirm → restore → reprocess gives a pending term.
3. **Legacy backup.** `alt["<box>:<k>:legacy"]` is written once, the first time a list holding plain strings or entries without src is replaced, and is never overwritten. The frontend's swap/merge slot `alt["<box>:<k>"]` behaves as before. This covers reprocess, summary, notes, enhance and notes_enhance. Tested.
4. **`/mode`** now puts `mode` in the job payload. `meeting.mode` is set only in the job's final write. Tested: a failed or cancelled job keeps the old mode.
5. **Skipped steps.** If the correction or slips step fails permanently, `meeting.warnings` gets 「名詞校正失敗，已略過：…」 or 「檢查口誤失敗，已略過：…」 and the status is still done.
   - The warnings appear in the md header as `> ⚠️ …` and in the docx.
   - A clean run clears them.
   - `warnings` is server-owned on PUT.
   - Tested.
6. **`/review`** returns 409 「整理中，請稍後再確認」 while the meeting is summarizing with a queued or running `aiJobId`. Tested.
7. **Term apply** now settles every pending pair with the same `wrong`. Tested.
8. **`verify_review`** now calls `verify_meeting` only. The second merge and my own carry-over of edited items are gone, because Task B's `merge_review` now carries state by kind+section+itemId.

Note: the smoke numbers above were measured before this round. With these rules, many of the 70 auto-applied pairs will become pending, for example the 2-character 證與→贈與 and 結會→結匯. That shrinks silent edits but lengthens the 待確認清單. A rerun in Task E will show the new balance.

## Fix round 2

The server suite has **224 tests passing**: 6 new tests in `test_reprocess_fix1.py`, and 2 tests from round 1 updated to the new rulings. Only `review.py`, `ai.py` and tests were edited.

- **A. A user's confirm or edit of a term now replaces every occurrence in the transcript.** The one exception is a match that sits inside an existing occurrence of `right`. Automatic application keeps the strict ±15 s window rules.
  - The term review item's `detail` is rewritten after verify by `review.term_detail`. Task B's `verify.term_items` stays untouched; its 「出現 N 次」 count is dropped.
  - The detail always gives the exact total: 「確認後全文 N 處都會改成「right」」.
  - When the wrong word is short (fewer than 3 CJK characters and not a Latin token) or has more than 5 occurrences, the detail also lists up to 5 contexts, formatted `[HH:MM:SS] …8 chars【wrong】8 chars…`. Look-alikes such as 保【證與】否 show up there, followed by 「若其中有不該改的，請用「改正」或「刪除」」.
  - Tests:
    - 7 occurrences → confirm leaves 0, and count=7.
    - A 2-character wrong word gets the context list, including 保證與否.
    - A long word with a single occurrence gets no list.
  - The round-1 test that expected 保證與否 to stay untouched now expects it to change, per the ruling.
- **C. Settling a term no longer creates duplicate entries.** All pending pairs with the same wrong word merge into the existing (wrong, right) entry, even one that was rejected, or into a single entry if none exists. The other pending pairs are removed.
- **D. Reruns keep an earlier failure note.** On `/mode` and `/restore-raw`, the correction step does not run, so an earlier 「名詞校正失敗…」 warning is kept. On `/mode`, the slip check does not run either, so an earlier 「檢查口誤失敗…」 warning is kept too. Tested for both routes.
- **E. No chaining within one correction batch.** A pair whose wrong word equals the right word of a pair already applied in the same batch is skipped. Tested: 維新群島→維京群島 followed by 維京群島→英屬維京群島.
