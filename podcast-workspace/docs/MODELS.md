# Models and licences

*Generated from `config/models.yaml` by `scripts/gen_models_md.py`. Licence, size and revision facts were read from the Hugging Face API on **2026-10-05**. Hugging Face metadata is a hint, not legal advice: read each model card and licence text before commercial use, and check training-data and dependency terms separately.*

**Status words** (strict): `working` = exercised end-to-end in this repo's tests; `implemented-untested` = code exists, never run against the real service; `placeholder` = stub only; `candidate` = researched, no adapter; `blocked` = licence forbids commercial use.

**Licence classes**: `permissive` (e.g. Apache-2.0, MIT) · `open-weight-restricted` (use-based or custom terms such as OpenRAIL, or an API with its own terms) · `non-commercial` (disabled by default).


## Script / scene-plan LLMs

| Model | Status | Licence | Class | Weights | Suggested GPU | Runs on | Pinned revision |
|---|---|---|---|---|---|---|---|
| Built-in template writer (no model)<br><code>template-local</code> | working | this repo | permissive | — | — | local | `—` |
| Any OpenAI-compatible chat endpoint (vLLM, Ollama, OpenRouter, OpenAI...)<br><code>openai-compatible</code> | implemented-untested | depends on the model you point it at | open-weight-restricted | — | — | external-api | `—` |
| Anthropic Messages API<br><code>anthropic-api</code> | implemented-untested | Anthropic commercial API terms (not open source) | open-weight-restricted | — | — | external-api | `—` |
| [Qwen3-30B-A3B-Instruct-2507 (self-hosted behind an OpenAI-compatible server)](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507)<br><code>qwen3-30b-a3b-instruct-2507</code> | candidate | Apache-2.0 | permissive | 61.1 GB | H100 | modal | `0d7cf23991` |

- `template-local`: Deterministic placeholder dialogue so the zero-credential 30 s proof runs. NOT an LLM; refused above 60 s.
- `openai-compatible`: Tested only against a local mock server. Prompt/response content leaves this host.
- `anthropic-api`: Tested only against a local mock server. Not open-weight; listed because script quality matters most for a 20-minute episode.
- `qwen3-30b-a3b-instruct-2507`: Cheaper to call a hosted API for one script per episode than to keep this on a GPU.

## Text-to-speech

| Model | Status | Licence | Class | Weights | Suggested GPU | Runs on | Pinned revision |
|---|---|---|---|---|---|---|---|
| [Kokoro-82M (ONNX int8, CPU)](https://huggingface.co/hexgrad/Kokoro-82M)<br><code>kokoro-onnx-local</code> | working | Apache-2.0 | permissive | 0.12 GB | none | local | `f3ff357179` |
| [Chatterbox (Resemble AI)](https://huggingface.co/ResembleAI/chatterbox)<br><code>chatterbox</code> | placeholder | MIT | permissive | 13.9 GB | L4 | modal | `5bb1f6ee58` |
| [VibeVoice 1.5B (long-form multi-speaker)](https://huggingface.co/microsoft/VibeVoice-1.5B)<br><code>vibevoice-1.5b</code> | candidate | MIT (HF metadata) | permissive | 5.4 GB | L4 | modal | `c00898d257` |
| [Dia 1.6B (dialogue)](https://huggingface.co/nari-labs/Dia-1.6B)<br><code>dia-1.6b</code> | candidate | Apache-2.0 | permissive | 12.9 GB | L4 | modal | `257bc72f9b` |
| [F5-TTS](https://huggingface.co/SWivid/F5-TTS)<br><code>f5-tts</code> | blocked | CC-BY-NC-4.0 (weights) | non-commercial | — | — | modal | `—` |
| [Coqui XTTS-v2](https://huggingface.co/coqui/XTTS-v2)<br><code>xtts-v2</code> | blocked | Coqui Public Model License | non-commercial | — | — | modal | `—` |

- `kokoro-onnx-local`: Fast and clearly synthetic-but-pleasant. Fixed voice pack (no cloning). Used for the free proof.
- `chatterbox`: More expressive than Kokoro and can clone a reference voice (only use voices you have rights to). Cloud function not yet written.
- `vibevoice-1.5b`: Designed for multi-speaker podcasts. Check the repo/model card for current availability and usage restrictions before adopting.
- `f5-tts`: Weights are non-commercial.

## Image generation / visuals

| Model | Status | Licence | Class | Weights | Suggested GPU | Runs on | Pinned revision |
|---|---|---|---|---|---|---|---|
| Procedural illustrated hosts and studio (Pillow)<br><code>procedural-draft</code> | working | this repo | permissive | — | — | local | `—` |
| [FLUX.1 [schnell]](https://huggingface.co/black-forest-labs/FLUX.1-schnell)<br><code>flux1-schnell</code> | placeholder | Apache-2.0 | permissive | 57.8 GB | L40S | modal | `741f7c3ce8` |
| [Qwen-Image](https://huggingface.co/Qwen/Qwen-Image)<br><code>qwen-image</code> | candidate | Apache-2.0 | permissive | 57.7 GB | L40S | modal | `75e0b4be04` |
| [FLUX.1 [dev]](https://huggingface.co/black-forest-labs/FLUX.1-dev)<br><code>flux1-dev</code> | blocked | FLUX.1 [dev] Non-Commercial License | non-commercial | — | — | modal | `—` |
| [Stable Diffusion XL base 1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)<br><code>sdxl-base</code> | candidate | OpenRAIL++-M | open-weight-restricted | — | — | modal | `—` |

- `procedural-draft`: Stylised cartoon animatic, NOT realistic. Exists to prove timing, cuts, captions, watermark, audio and the MP4 path for free.
- `flux1-schnell`: Repo is gated (accept terms on Hugging Face, needs HF_TOKEN). Repo holds multiple formats; installer must filter files (~24-34 GB needed).

## Video generation

| Model | Status | Licence | Class | Weights | Suggested GPU | Runs on | Pinned revision |
|---|---|---|---|---|---|---|---|
| [Wan2.2-TI2V-5B (text/image-to-video, no audio driving)](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B)<br><code>wan2.2-ti2v-5b</code> | candidate | Apache-2.0 | permissive | 34.2 GB | L40S | modal | `921dbaf3f1` |
| [LTX-Video](https://huggingface.co/Lightricks/LTX-Video)<br><code>ltx-video</code> | candidate | custom "other" licence | open-weight-restricted | — | — | modal | `—` |

## Lip-sync / audio-driven video

| Model | Status | Licence | Class | Weights | Suggested GPU | Runs on | Pinned revision |
|---|---|---|---|---|---|---|---|
| [InfiniteTalk (audio-driven image-to-video / video dubbing, built on Wan)](https://huggingface.co/MeiGen-AI/InfiniteTalk)<br><code>infinitetalk</code> | placeholder | Apache-2.0 (HF metadata; code repo not checked from this sandbox) | permissive | 168.6 GB | H100 | modal | `d59847ebda` |
| [Wan2.2-S2V-14B (speech-to-video)](https://huggingface.co/Wan-AI/Wan2.2-S2V-14B)<br><code>wan2.2-s2v-14b</code> | candidate | Apache-2.0 | permissive | 49.1 GB | H100 | modal | `dab4e9c55b` |
| [LatentSync 1.6 (video-to-video lip-sync, mouth region only)](https://huggingface.co/ByteDance/LatentSync-1.6)<br><code>latentsync-1.6</code> | candidate | OpenRAIL++ (HF metadata) | open-weight-restricted | 9.6 GB | L40S | modal | `c42c7e6c8e` |
| [MuseTalk (real-time lip-sync)](https://huggingface.co/TMElyralab/MuseTalk)<br><code>musetalk</code> | candidate | MIT (HF metadata); dependencies carry their own licences | permissive | 6.8 GB | L4 | modal | `2bcb936e2f` |

- `infinitetalk`: Moves head and upper body, not just lips - closest match to the brief. Never run by us yet; entrypoint command still to be verified against its README.
- `latentsync-1.6`: Cheap option that needs an existing video of the person; fixes mouths, adds no body motion.
