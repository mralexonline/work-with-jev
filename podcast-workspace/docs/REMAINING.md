# What is still required from you (accounts, credentials, decisions)

Nothing here was available during the build, so nothing here has been used. Order = suggested order. **Nothing is created or
billed until you do it yourself or approve it.**

## To run the free path on a server (optional)
1. **A host** with persistent disk for `DATA_DIR` (any small VM or your own machine). Needs Python 3.11, ffmpeg (libass, freetype),
   `fonts-dejavu-core`. *Decision:* where; ~4 CPU cores recommended for draft rendering.
2. **A domain + HTTPS reverse proxy** (Caddy/nginx) if reachable from the internet; set `ALLOWED_ORIGINS` to the public origin.
3. **Owner token** — generated automatically on first run (`python -m app.cli show-token`) or set `OWNER_TOKEN`.

## To enable real scripts (needed for anything over 60 s)
4. **One LLM credential**, either: an **Anthropic API key** (`ANTHROPIC_API_KEY` + `LLM_MODEL`), or an **OpenAI-compatible endpoint**
   (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` — OpenAI, OpenRouter, or your own vLLM/Ollama). *Decision:* which provider/model.
   Cost per episode is expected to be small next to GPU time but is unmeasured. Enable it, then run a 60 s episode and read the script.

## To enable cloud GPU (Gates 1–3)
5. **Modal account** (modal.com; Starter lists $30/month free credit) and an **API token** (`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`
   or `modal token new` on the submitting machine). May require a payment method.
6. **Provider-side spend limit in Modal** (Settings → Usage and Billing → spend limit ≈ C$100 in USD). Strongly recommended
   *before* the first job.
7. **Hugging Face account + read token** (`HF_TOKEN`), and accept the **FLUX.1-schnell** terms on its model page (gated repo). Store it
   as a Modal secret named `huggingface` with key `HF_TOKEN`.
8. **Decisions:** `USD_PER_CAD`; `PER_JOB_CAP_CAD` (default C$25); whether Gate 1 may spend up to ~US$10; approval of the first
   `modal deploy` (image builds use CPU time) and of each weights download (storage is billed per GiB-month).
9. `pip install -r requirements-cloud.txt` (pins `modal==1.6.1`; unverified against these functions) and set `CLOUD_ENABLED=1`.

## Optional
10. **GitHub token** (read-only, public repos) to raise search rate limits: `GITHUB_TOKEN`.
11. **A container runtime** for stronger terminal isolation (`TERMINAL_CMD`).
12. **Voice rights**: if you want a specific voice cloned, only use audio you own or have permission to use.

## Engineering still to do (I will not claim these work until tested)
* Write and test the `tts`, `image`, `lipsync` Modal functions against the pinned revisions in `config/models.yaml`.
* Verify each model's real command line/VRAM/throughput from its README and a live run (Gate 1).
* Character-consistency strategy, wide-shot strategy, per-host idle loops, and the cloud branch of `runner.render_shots`.
* Word-aligned captions (e.g. Whisper) if the proportional timing is not good enough.
* Hosting/backups for `DATA_DIR` (SQLite + exports), log rotation, and a restore drill.
* Legal/policy review for publishing synthetic hosts and AI-generated media on the platforms you use (the app never publishes).
