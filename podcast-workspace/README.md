# Podcast Workspace

A browser-based chat workspace that turns one instruction — *"Create a 20-minute podcast with Matt and Chloe, three camera
angles, captions, my group name bottom-left"* — into a scripted, voiced, cut, captioned, downloadable MP4, using models you
select or add. Heavy inference is designed to run on cloud GPUs; the app server only coordinates.

> **Read this first — what is actually proven (2026-10-05).**
> The **free, credential-free path works end to end** and is tested against real processes: chat → plan → queue → script →
> real Kokoro voices → three-angle shot plan → frames driven by the real audio → captions + bottom-left group name + audio mix →
> verified MP4. **That video is a stylised cartoon animatic, not realistic video.** Realistic faces, lip-sync and body motion
> need cloud GPU models, whose wiring is **placeholder / untested** because it needs a paid account. A 15–20-minute episode is
> **not** proven. See [docs/STATUS.md](docs/STATUS.md) for the exact line between working and not.

| Capability | State |
|---|---|
| Chat UI, models/registry UI, jobs, logs (SSE), progress, cancel, preview, download, budget, terminal | **working** (tested) |
| Persistent queue, leases, bounded retries, crash resume, checkpoints, worker cleanup | **working** (tested incl. `kill -9`) |
| Spending controls: caps, approvals, usage ledger, measured-cost estimator | **working** (tested); provider-side limit is yours to set |
| Owner-only auth, secret redaction, same-origin checks, owner-only terminal | **working** (tested) |
| Search + inspect public Hugging Face / GitHub resources (licence, size, deps, hardware, compatibility) | HF inspect/search **implemented, mock-tested**; GitHub **mock-tested only** (blocked from this sandbox) |
| Script writing for 30 s | **working** with a deterministic template (placeholder text, refused > 60 s) |
| Script writing for 20 min via LLM (OpenAI-compatible or Anthropic) | **implemented, tested only against a mock server** |
| Natural-ish voices (Kokoro-82M, CPU) | **working** |
| Three camera angles, captions, bottom-left name, loudness-normalised mix | **working** |
| Realistic hosts, generative video, true lip-sync (FLUX / InfiniteTalk / Wan etc. on Modal) | **placeholder** — see [docs/STATUS.md](docs/STATUS.md) |
| Installing models into isolated cloud environments | **skeleton** (`cloud/modal_app.py`), never deployed |
| Custom adapter workflow for arbitrary repos | manifest scaffolding + validation **working**; runner **placeholder** |
| 15–20 minute production | **not attempted**; gated behind the cost/quality tests in STATUS.md |

## Quick start (no accounts, no cost)

Requirements: Python 3.11, `ffmpeg` with libass + freetype, `fonts-dejavu-core`, ~1 GB disk.

```bash
cd podcast-workspace
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock
python scripts/fetch_models.py          # Kokoro voices (~120 MB), sha256-verified
python -m app.main &                    # http://127.0.0.1:8000
python -m app.worker &                  # the job runner
python -m app.cli show-token            # owner token for the sign-in page
```

Open <http://127.0.0.1:8000>, sign in, and send:

> Create a 30-second podcast with Matt and Chloe about neighbourhood events, three camera angles, captions, and
> “Durham & Clarington Community REAL TALK” at the bottom left.

Review the plan card, press **Start (free)**, watch progress/logs, then preview and download the MP4. A 30-second draft takes
about a minute on 4 CPU cores. Nothing is published anywhere.

## Verify it yourself

```bash
python -m pytest -q tests                # 84 unit/API tests, a few seconds
python scripts/verify_e2e.py             # real server + worker: 30 s episode, cancel, kill -9 resume, terminal (~5 min, US$0)
```
The latest transcript is [docs/VERIFICATION.md](docs/VERIFICATION.md); a sample output is `docs/sample_30s_draft.mp4`.

## Documentation

| | |
|---|---|
| [docs/STATUS.md](docs/STATUS.md) | Working vs placeholder, and the gated path to 15–20 minute production |
| [docs/REMAINING.md](docs/REMAINING.md) | Exactly which accounts, credentials and decisions you still need to supply |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data flow, why this shape |
| [docs/COSTS.md](docs/COSTS.md) | C$100/month budget, idle and storage charges, estimates and what they assume |
| [docs/MODELS.md](docs/MODELS.md) | Models, licences, commercial-use class, pinned revisions |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, controls, terminal isolation, known limits |
| [docs/SETUP.md](docs/SETUP.md) | Local run, server deployment, Modal setup (approval-gated) |
| [docs/CUSTOM_ADAPTERS.md](docs/CUSTOM_ADAPTERS.md) | Adding a repo that is not in the registry |

## Layout

```
app/            FastAPI app, queue, budget, registry, research, installer, terminal, worker
app/adapters/   LLM / TTS adapters (+ cloud adapter stubs)
app/pipeline/   spec, script, timeline (voice, shots, captions), draft renderer, assembly, runner
cloud/          Modal app skeleton (the only place third-party model code may run)
config/         models.yaml (registry), pricing.yaml (price list + assumptions)
frontend/       plain HTML/JS/CSS, vendored xterm.js 6.0.0 (MIT)
scripts/        fetch_models, verify_e2e, gen_models_md
tests/          unit + API tests
```

No video is ever published automatically; the app has no publishing code at all.
