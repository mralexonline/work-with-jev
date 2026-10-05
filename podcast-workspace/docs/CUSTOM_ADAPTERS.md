# Adding a model or repository that is not in the registry

Arbitrary repositories will **not** run automatically. Many need a specific CUDA/torch build, private weights, patched
dependencies, or have licences that forbid your use. The workflow makes a human review each one.

1. **Inspect** (`inspect <url>` in chat, or Models → Inspect). You get licence class, revision SHA, repo/weights size, a VRAM
   *estimate*, parsed dependencies with risk flags (unpinned, URL/VCS deps, `insightface`, `flash-attn`, …) and a verdict:
   `registered` or `custom-adapter-required`. Non-commercial and unknown licences are flagged.
2. **Scaffold** (button on the report, or `POST /api/research/scaffold`). Writes `adapters_custom/<slug>/adapter.yaml` — a *draft*:
   source repo + revision, licence, runtime (python/lockfile/gpu/timeout), entrypoint, review block. No code is downloaded.
3. **Review by hand.** Read the code you will run. Pin `source.revision` to a full 40-character commit SHA. Produce a
   `requirements.lock`. Replace each `TODO` with the exact, manually verified command from the project README. Only then set
   `review.reviewed_by_owner: true`.
4. **Validate**: `installer.validate_manifest()` rejects: non-40-char revision, unreviewed, non-commercial/unknown licence class,
   leftover `TODO`s.
5. **Run** (not built yet): a generic Modal function will build a pinned image from the manifest, clone the pinned commit *inside
   the container*, and execute the entrypoint with `job.json` → `result.json`. Until then a custom adapter can be run by hand in
   Modal, or promoted to a real adapter class + registry entry (start at `placeholder`).

**I/O contract for an adapter job** (so renderers stay swappable): input `job.json` with `{kind, text|image|audio paths, seed,
size}`; output files in `/outputs/<job_id>/` plus `result.json` with `{files: [...], gpu: "H100", gpu_seconds: 123.4}`. Reporting
`gpu` and `gpu_seconds` is mandatory — it is how spend is tracked.
