# Setup

## 1. Local, free (no accounts)
See the README quick start. Needs Python 3.11, `ffmpeg` (with libass and freetype: `ffmpeg -filters | grep -E "subtitles|drawtext"`),
and DejaVu fonts at `/usr/share/fonts/truetype/dejavu/` (`apt install fonts-dejavu-core`).

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock
python scripts/fetch_models.py     # verifies pinned sha256; fails loudly on mismatch
python -m app.main & python -m app.worker &
python -m app.cli show-token
```
Run more than one worker if you have CPU to spare; leases make claims exclusive.

## 2. On a server
* Create a dedicated unprivileged user; keep `.env` mode 0600; put `DATA_DIR` on a persistent disk and **back it up** (it holds the
  SQLite queue/ledger and the finished MP4s).
* Run `python -m app.main` and `python -m app.worker` under systemd (or use `docker-compose.yml`, *unbuilt here*).
* HTTPS reverse proxy (Caddy: `reverse_proxy 127.0.0.1:8000`). Set `ALLOWED_ORIGINS=https://your.host`. Keep `HOST=127.0.0.1`.
* Optional: `ENABLE_TERMINAL=1` (read [SECURITY.md](SECURITY.md) first).

## 3. Cloud GPU (every step that can spend money needs your say-so)
Do these in order; stop whenever the numbers look wrong. Read [COSTS.md](COSTS.md) first.

1. **[free]** Create the Modal account; create an API token; in *Usage and Billing* set the **spend limit** (≈ US$72 for C$100).
2. **[free]** `pip install -r requirements-cloud.txt`; `modal token new` on the submitting machine.
3. **[free]** Create the secret: `modal secret create huggingface HF_TOKEN=<read token>`; accept the FLUX.1-schnell terms on Hugging Face.
4. **[small CPU cost — approve]** `modal deploy cloud/modal_app.py` builds the pinned base image and registers functions.
5. **[storage per GiB-month + CPU download — approve per model]** Download weights into the volume with
   `modal run cloud/modal_app.py::download_weights --repo <id> --revision <sha from models.yaml>` (use `allow_patterns` to skip variants). *The exact `modal run` argument syntax for list parameters is unverified; you may need a small `@app.local_entrypoint()` wrapper.*
6. Set `CLOUD_ENABLED=1`. Paid jobs then appear as **awaiting approval** in the UI with an estimate; you approve with a cap.
7. Gate 1 (30 s GPU proof) per [STATUS.md](STATUS.md). The functions are placeholders until that step produces real command lines.

To stop all cloud spend: `modal app stop podcast-workspace`, set `CLOUD_ENABLED=0`, and delete unused volumes
(`modal volume delete podcast-workspace-weights`). Idle containers scale to zero by themselves.

## 4. Configuration reference
Every setting is an environment variable documented in `.env.example` (no secret has a default).

## 5. Troubleshooting
* *"template writer only supports episodes up to 60s"*: configure an LLM (REMAINING.md #4) or ask for ≤ 60 s.
* *Kokoro files missing*: `python scripts/fetch_models.py` (needs network to github.com release assets).
* *Job stuck "queued"*: no worker is running (`python -m app.worker`), or it is waiting for backoff after a failure.
* *Job "awaiting approval"*: by design for anything paid; approve in the job card.
* Inspect the queue: `python -m app.cli cleanup` re-queues jobs whose worker died; `python -m app.cli usage` prints the ledger.
