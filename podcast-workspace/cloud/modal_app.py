"""Modal app: the ONLY place model/repository code executes.

STATUS: infrastructure skeleton. `download_weights` is complete but untested; `tts`, `image`
and `lipsync` are deliberately placeholders. Nothing here has been deployed or run, because it
needs a Modal account. See docs/STATUS.md for what must happen before they are real.

Safety properties built in:
  * images are built from PINNED packages and a PINNED model revision (see config/models.yaml);
  * no inbound network is exposed: functions are invoked via the Modal SDK with the owner's token;
  * containers scale to zero (min_containers default 0) and use a short scaledown window so idle
    GPU time is small and bounded;
  * every function reports `gpu` and `gpu_seconds` so the app records MEASURED spend;
  * a hard `timeout=` on each function bounds a runaway job.

Deploy (after you approve the cost, docs/COSTS.md):  modal deploy cloud/modal_app.py
"""
import functools
import os
import time

import modal

APP_NAME = os.environ.get("MODAL_APP_NAME", "podcast-workspace")
app = modal.App(APP_NAME)

# Persistent volume for weights (billed per GiB-month, see docs/COSTS.md).
weights = modal.Volume.from_name(f"{APP_NAME}-weights", create_if_missing=True)
outputs = modal.Volume.from_name(f"{APP_NAME}-outputs", create_if_missing=True)
WEIGHTS_DIR, OUT_DIR = "/weights", "/outputs"

# Pin everything. Bump deliberately, never with a floating tag.
base_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git")
    .pip_install("huggingface_hub==0.34.4", "numpy==2.2.6")
)

SCALEDOWN_S = int(os.environ.get("SCALEDOWN_S", "10"))  # idle tail that is still billed (min 2, default 60)


@app.function(image=base_image, volumes={WEIGHTS_DIR: weights}, timeout=60 * 60,
              secrets=[modal.Secret.from_name("huggingface", required_keys=["HF_TOKEN"])],
              scaledown_window=SCALEDOWN_S)
def download_weights(repo: str, revision: str, allow_patterns: list[str] | None = None) -> dict:
    """Copy a PINNED Hugging Face revision into the weights volume (CPU only, no GPU billed)."""
    from huggingface_hub import snapshot_download

    t0 = time.time()
    path = snapshot_download(repo_id=repo, revision=revision, local_dir=f"{WEIGHTS_DIR}/{repo}",
                             allow_patterns=allow_patterns, token=os.environ.get("HF_TOKEN"))
    weights.commit()
    return {"path": path, "seconds": time.time() - t0, "gpu": None, "gpu_seconds": 0}


def _gpu_timed(fn):
    """Decorator: report wall-clock GPU seconds so the app can bill itself accurately."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        t0 = time.time()
        out = fn(*a, **k)
        out["gpu_seconds"] = time.time() - t0
        return out
    return wrapper


@app.function(image=base_image, gpu="L4", timeout=15 * 60, scaledown_window=SCALEDOWN_S,
              volumes={WEIGHTS_DIR: weights, OUT_DIR: outputs})
@_gpu_timed
def tts(job: dict) -> dict:
    raise NotImplementedError("PLACEHOLDER: Chatterbox/VibeVoice wrapper not written or tested yet")


@app.function(image=base_image, gpu="L40S", timeout=15 * 60, scaledown_window=SCALEDOWN_S,
              volumes={WEIGHTS_DIR: weights, OUT_DIR: outputs})
@_gpu_timed
def image(job: dict) -> dict:
    raise NotImplementedError("PLACEHOLDER: FLUX.1-schnell wrapper not written or tested yet")


@app.function(image=base_image, gpu="H100", timeout=45 * 60, scaledown_window=SCALEDOWN_S,
              volumes={WEIGHTS_DIR: weights, OUT_DIR: outputs})
@_gpu_timed
def lipsync(job: dict) -> dict:
    raise NotImplementedError("PLACEHOLDER: InfiniteTalk wrapper not written or tested yet; "
                              "verify its README command in the 30 s GPU proof first")
