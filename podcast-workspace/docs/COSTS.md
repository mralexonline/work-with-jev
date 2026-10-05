# Costs, limits, idle and storage charges

Budget: **≈ C$100 / month**. The app converts with `USD_PER_CAD` (default 0.72 → **US$72**). That rate is a placeholder — the app
cannot know today's rate, so set it. Everything below is USD unless marked.

## Prices used (read from modal.com/pricing on 2026-10-05, stored in `config/pricing.yaml`)

| GPU | US$/second | US$/hour |
|---|---|---|
| L4 | 0.000222 | 0.80 |
| L40S | 0.000542 | 1.95 |
| A100 80 GB | 0.000694 | 2.50 |
| H100 | 0.001097 | 3.95 |

CPU 0.0000131/core-s, memory 0.00000222/GiB-s, volume storage 0.09/GiB-month (the page also says "includes 1 TiB free" — verify what
that means on your plan before relying on it), egress 0.04/GiB. The Starter plan lists **$30/month free credits** and no base fee.
Prices change; re-read the page before approving spend. Other providers (RunPod, etc.) were not evaluated.

## What costs money, and when

| Item | When it bills | Notes |
|---|---|---|
| GPU while a function runs | per second, only while a container is up | includes model load/cold start — a 14B model can take a minute or more to load. **Measure it.** |
| **Idle tail** | after each call the container stays warm for `scaledown_window` (default 60 s; we set 10 s via `SCALEDOWN_S`; range 2 s–20 min) and that tail **is billed** | 100 short calls = 100 tails. Batch work into few long calls. `min_containers` must stay 0, otherwise a GPU bills 24/7. |
| Scaled to zero | nothing | no idle GPU charge. |
| Weights volume | per GiB-month **even when nothing runs** | see below; this is the main recurring line besides the app host |
| Image builds, weight downloads | CPU/memory seconds, like any function | one-off per pinned version |
| Egress | per GiB | a 20-minute 720p MP4 is a few hundred MB → cents |
| App host (small always-on VM) | monthly | typically a few US$ to ~10; **not verified here** — check your provider. Not part of the Modal bill. |
| LLM script writing | per token (hosted API) | one episode ≈ a few thousand words in/out; expected to be small vs GPU. **Not measured.** |

### Storage, concretely
Registry weights sizes (Hugging Face API, 2026-10-05): InfiniteTalk repo 168.6 GB (many variants; an install must select a subset
plus the Wan base weights), Wan2.2-S2V-14B 49.1 GB, FLUX.1-schnell repo 57.8 GB (~24–34 GB actually needed), Chatterbox 13.9 GB,
LatentSync 9.6 GB. A realistic first install is **100–250 GB**. At the list price that is **US$9–22 per month** if no allowance
applies, i.e. 12–30 % of the budget *before any generation*. Keep only the weights you use; the app shows size before install.

## Estimating a 20-minute episode (honest version)

The dominant cost is audio-driven video generation. **Nobody has measured how many GPU-seconds InfiniteTalk-class models need per
output second on this pipeline; the number in `pricing.yaml` (30 on an H100) is an assumption.** Sensitivity, H100 only:

| GPU-s per output-s | 20-min lip-sync cost |
|---|---|
| 10 | US$13 |
| 20 | US$26 |
| 30 (assumed) | US$40 |
| 50 | US$66 |
| 60 | US$79 — **over the whole US$72 budget** |
| 100 | US$132 |

Break-even for a single US$70 episode is ≈ 53 GPU-s per output-s, leaving nothing for retries, storage, the app host or a
second episode. The other stages are small on the same assumptions (TTS ≈ US$0.13, stills ≈ US$0.04). The estimator in the UI
shows a typical and a high (×3) figure and labels every row `ASSUMED` until a real run exists; after a run it switches to the
**measured median**. Treat the high figure as the planning number.

If measured throughput is too slow/expensive, the levers (to be tested, not promised): lower resolution (480p generation, upscale
in assembly); generate lip-sync only for close-ups and keep the wide shot largely static or reuse short idle loops; cheaper
mouth-only lip-sync (LatentSync/MuseTalk) over a pre-generated base loop of each host; shorter episodes; one episode per month.
It is plausible that **one 20-minute fully generative episode per month is the realistic ceiling at C$100**; the 30-second proofs
will tell.

## Controls in this repo

* `MONTHLY_BUDGET_CAD`, `PER_JOB_CAP_CAD`, `SOFT_ALERT_PCT` (env). The UI header shows month-to-date.
* **Approval gate**: paid jobs (cloud GPU, paid LLM, cloud installs) sit in `awaiting_approval`. You approve with a cap in US$;
  approval is refused if `spent + reserved + cap` exceeds the monthly budget or the cap exceeds the per-job limit.
* **Per-call guard**: before every cloud call `budget.enforce_job_cap` refuses to start if it could cross the job cap or the month.
* **Measured ledger**: cloud functions return `gpu` and `gpu_seconds`; cost = seconds × list price, stored in `usage`.
  Cloud calls are `cancel()`ed on cancel/failure so billing stops.
* **Second, independent cap (yours to set)**: in Modal, *Settings → Usage and Billing*: a *workspace budget* caps usage before credits;
  a *spend limit* caps net out-of-pocket charges and stops workloads that would exceed it (per Modal's budgets guide, read
  2026-10-05). Set the spend limit to about this budget. This app cannot see charges Modal has not reported to it.
* Approval is **not** needed for the free local path, which never touches a paid service.
