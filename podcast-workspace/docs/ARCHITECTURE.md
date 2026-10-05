# Architecture

```
 Browser (plain JS)                    App host (small VM, always on)                         Cloud GPU (Modal, scale-to-zero)
 ┌────────────────────┐  HTTPS   ┌─────────────────────────────────────────┐  SDK spawn   ┌───────────────────────────────┐
 │ chat · models      │─────────▶│ FastAPI  (auth, REST, SSE, media, WS)   │─────────────▶│ pinned images, weights volume │
 │ jobs · budget      │◀─────────│   │                                     │◀─────────────│ tts / image / lipsync fns     │
 │ preview · terminal │   SSE    │   ▼                                     │  results +   │ (only place third-party model │
 └────────────────────┘          │ SQLite (WAL) on persistent disk         │  gpu_seconds │  code runs)   [UNTESTED]      │
                                 │  projects · jobs(queue) · events        │              └───────────────────────────────┘
                                 │  checkpoints · usage ledger · chat      │
                                 │   ▲                                     │
                                 │ Worker(s) `python -m app.worker`        │
                                 │  script → voice → plan → render →       │
                                 │  assemble(ffmpeg) → verify(ffprobe)     │
                                 │ projects/<id>/…  exports/episode.mp4    │
                                 └─────────────────────────────────────────┘
```

## Why this shape

* **One language, one datastore.** Python + SQLite (WAL) is enough for one owner and a handful of long jobs. The queue is a
  table with leases; there is no Redis/Celery to run, patch or pay for. If you outgrow it, `app/jobq.py` is the only module that
  knows the queue exists.
* **The app server never runs model code.** It builds JSON jobs, calls Modal, and receives files. Third-party repositories are
  cloned and executed only inside Modal containers built from pinned images ([SECURITY.md](SECURITY.md)).
* **Everything expensive is checkpointed.** Script segments, each spoken line, and each rendered shot are content-addressed
  (`checkpoints` table, key = hash of inputs). A crash, cancel, or closed browser costs at most the shot in flight; "resume"
  just re-queues the project and cached work is skipped. Tested with `kill -9` (docs/VERIFICATION.md, scenario C).
* **One assembly path for every renderer.** Renderers (the free draft renderer today; cloud image-to-video/lip-sync later) emit
  per-shot H.264 clips with no audio/overlays. `assemble.py` concatenates, mixes audio, burns captions and the bottom-left name.
* **Spending is gated twice.** Inside the app (caps, approval, reservation, per-call check) and at the provider (Modal workspace
  spend limit — you set it). See [COSTS.md](COSTS.md).

## Job lifecycle

`awaiting_approval` (paid only) → `queued` → `running` → `succeeded | failed | cancelled`.
Failures retry with exponential backoff up to `MAX_ATTEMPTS`; `NotConfigured`, `BudgetExceeded` and validation errors never retry.
A running job holds a lease renewed by a heartbeat thread; a crashed worker's lease expires and the job is re-queued (counts as
an attempt). Cancel sets a flag checked every second of rendered video and between stages; cloud calls are `cancel()`ed so
billing stops; `.part` files are removed.

## Podcast pipeline

1. **Script** — outline → per-segment dialogue (≈2.5 words/s) via the LLM adapter; markdown/stage directions stripped; speakers validated.
2. **Voice** — one TTS call per line (cached by text+voice+model), assembled with seeded natural pauses; per-speaker mouth-energy
   envelopes computed from the real audio.
3. **Plan** — shot list: open wide, individual close-ups on the *current* speaker (never on the listener), wide for short
   interjections and every few turns, long monologues broken by cutaways; ASS + SRT captions.
4. **Render** — per-shot clips in parallel processes (draft renderer) — or, later, cloud image/video/lip-sync adapters.
5. **Assemble** — ffmpeg concat, `acompressor`+`loudnorm` (-16 LUFS) with quiet pink room-tone, ASS captions, `drawtext` group name.
6. **Verify** — ffprobe: duration vs audio timeline, resolution, audio stream present; failure fails the job.

Caption timing is proportional to character count within each line (no forced alignment), so words can drift by a fraction of a
second inside long lines. Word-level alignment would need a model such as Whisper; not implemented.

## Extending

* New model: implement an adapter from `app/adapters/base.py`, add an entry to `config/models.yaml` (licence, revision, hardware,
  status), run `python scripts/gen_models_md.py`. Start its status at `placeholder`/`implemented-untested`.
* New repo not in the registry: [CUSTOM_ADAPTERS.md](CUSTOM_ADAPTERS.md).
