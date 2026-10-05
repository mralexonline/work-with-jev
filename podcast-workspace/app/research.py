"""Search and inspect PUBLIC GitHub / Hugging Face resources. Read-only metadata only.

Nothing here downloads or executes repository code. `inspect_*` reads API metadata and a few
small text files (requirements, README head) and turns them into a compatibility report.
"""
from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import yaml

from .config import ROOT, get_settings

HF = "https://huggingface.co"
GH = "https://api.github.com"

# Licence classification. Anything not recognised is "unknown" and treated as NOT commercially usable
# until a human reads it.
PERMISSIVE = {"apache-2.0", "mit", "bsd-3-clause", "bsd-2-clause", "isc", "unlicense", "cc0-1.0", "cc-by-4.0", "mpl-2.0"}
RESTRICTED = {"openrail", "openrail++", "creativeml-openrail-m", "bigscience-openrail-m", "llama2", "llama3", "llama3.1",
              "llama3.2", "llama3.3", "gemma", "other"}
NON_COMMERCIAL = {"cc-by-nc-4.0", "cc-by-nc-sa-4.0", "cc-by-nc-nd-4.0", "cc-by-nc-3.0", "gpl-3.0-nc"}
# Packages that commonly drag in non-commercial weights/terms or painful builds. A hint, not a verdict.
RISKY_DEPS = {
    "insightface": "ships/downloads models under a non-commercial research licence",
    "dlib": "needs a compiler toolchain; slow to build",
    "flash-attn": "compiles CUDA kernels; must match torch/CUDA exactly",
    "flash_attn": "compiles CUDA kernels; must match torch/CUDA exactly",
    "xformers": "must match the exact torch/CUDA build",
    "apex": "compiles CUDA extensions",
    "tensorflow": "second deep-learning framework; large image",
    "mmcv": "compiles against a specific torch build",
    "bitsandbytes": "CUDA-version-sensitive",
    "triton": "Linux/NVIDIA only",
}


def classify_license(lic: str | None) -> dict[str, Any]:
    key = (lic or "").strip().lower()
    if not key:
        return {"id": None, "class": "unknown", "commercial_use": None,
                "note": "No licence declared. Treat as all-rights-reserved until confirmed."}
    if key in PERMISSIVE:
        return {"id": key, "class": "permissive", "commercial_use": True, "note": "Verify dependencies and training-data terms separately."}
    if key in NON_COMMERCIAL or "-nc" in key:
        return {"id": key, "class": "non-commercial", "commercial_use": False, "note": "Not for commercial use."}
    if key in RESTRICTED or "rail" in key:
        return {"id": key, "class": "open-weight-restricted", "commercial_use": None,
                "note": "Custom or use-based terms. Read the licence text before commercial use."}
    return {"id": key, "class": "unknown", "commercial_use": None, "note": "Unrecognised licence id; read it."}


def parse_url(url: str) -> dict[str, str]:
    u = urlparse(url.strip())
    parts = [p for p in u.path.split("/") if p]
    host = (u.hostname or "").lower()
    if host in ("huggingface.co", "www.huggingface.co") and len(parts) >= 2 and parts[0] not in ("datasets", "spaces"):
        return {"type": "huggingface", "repo": f"{parts[0]}/{parts[1]}"}
    if host in ("github.com", "www.github.com") and len(parts) >= 2:
        return {"type": "github", "repo": f"{parts[0]}/{parts[1].removesuffix('.git')}"}
    raise ValueError("expected a public github.com/<owner>/<repo> or huggingface.co/<owner>/<model> URL")


def parse_requirements(text: str) -> list[dict[str, Any]]:
    out = []
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            if line.startswith(("git+", "http")):
                out.append({"name": line[:80], "pinned": False, "spec": "", "vcs_or_url": True})
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(.*)$", line)
        if m:
            spec = m.group(3).strip()
            out.append({"name": m.group(1).lower(), "pinned": spec.startswith("=="), "spec": spec})
    return out


def estimate_vram_gb(weights_gb: float, kind: str = "") -> dict[str, Any]:
    """Rough planning number: weights in half precision plus working memory. NOT a measurement."""
    need = weights_gb * 1.25 + 2
    gpu = "T4/L4 (16-24 GB)" if need <= 20 else "L40S/A100-40 (40-48 GB)" if need <= 44 else "A100-80/H100 (80 GB)" if need <= 76 else "multi-GPU or quantised weights"
    return {"estimate_gb": round(need, 1), "suggested_gpu": gpu, "basis": "weights x1.25 + 2 GB; verify with the model card"}


class Research:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.client = httpx.Client(timeout=30, transport=transport, follow_redirects=True)
        self.hf_headers = {"Authorization": f"Bearer {s.hf_token}"} if s.hf_token else {}
        self.gh_headers = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {s.github_token}"} if s.github_token else {})}

    # ---------------------------------------------------------------- search
    def search_hf(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        r = self.client.get(f"{HF}/api/models", params={"search": query, "limit": limit, "sort": "downloads", "direction": -1},
                            headers=self.hf_headers)
        r.raise_for_status()
        return [{"type": "huggingface", "repo": m["id"], "downloads": m.get("downloads"), "likes": m.get("likes"),
                 "pipeline": m.get("pipeline_tag"), "url": f"{HF}/{m['id']}"} for m in r.json()]

    def search_github(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        r = self.client.get(f"{GH}/search/repositories", params={"q": query, "sort": "stars", "per_page": limit}, headers=self.gh_headers)
        r.raise_for_status()
        return [{"type": "github", "repo": i["full_name"], "stars": i["stargazers_count"], "description": i.get("description"),
                 "license": (i.get("license") or {}).get("spdx_id"), "pushed_at": i.get("pushed_at"), "url": i["html_url"]}
                for i in r.json()["items"]]

    # ---------------------------------------------------------------- inspect
    def inspect_hf(self, repo: str) -> dict[str, Any]:
        r = self.client.get(f"{HF}/api/models/{quote(repo, safe='/')}", params={"blobs": "true"}, headers=self.hf_headers)
        r.raise_for_status()
        d = r.json()
        card = d.get("cardData") or {}
        lic = card.get("license") or next((t.split(":", 1)[1] for t in d.get("tags", []) if t.startswith("license:")), None)
        size = sum((s.get("size") or 0) for s in d.get("siblings", [])) / 1e9
        weights = sum((s.get("size") or 0) for s in d.get("siblings", []) if s["rfilename"].endswith((".safetensors", ".bin", ".pt", ".pth", ".onnx", ".gguf", ".ckpt"))) / 1e9
        files = [s["rfilename"] for s in d.get("siblings", [])]
        has_custom_code = any(f.endswith(".py") for f in files) or "custom_code" in d.get("tags", [])
        rep = {
            "type": "huggingface", "repo": repo, "revision": d.get("sha"), "gated": d.get("gated", False),
            "license": classify_license(lic), "license_name": card.get("license_name"), "pipeline": d.get("pipeline_tag"),
            "library": d.get("library_name"), "tags": d.get("tags", [])[:25], "downloads": d.get("downloads"),
            "last_modified": d.get("lastModified"), "repo_size_gb": round(size, 1), "weights_gb": round(weights, 1),
            "hardware": estimate_vram_gb(min(weights, size) if weights else size),
            "contains_python_files": has_custom_code, "dependencies": [], "warnings": [],
        }
        if d.get("gated"):
            rep["warnings"].append("Gated: accept the terms on huggingface.co and provide HF_TOKEN to download.")
        wfiles = [f for f in files if f.endswith((".safetensors", ".bin", ".pt", ".pth", ".onnx", ".gguf", ".ckpt"))]
        formats = {f.rsplit(".", 1)[-1] for f in wfiles}
        if rep["repo_size_gb"] > 20 and (len(formats) >= 2 or len(wfiles) >= 8):
            rep["warnings"].append("Repo holds more than one format/variant; an install must select files (allow_patterns) to avoid paying for storage you do not need.")
        return self._finish(rep, registry_match=repo)

    def inspect_github(self, repo: str) -> dict[str, Any]:
        r = self.client.get(f"{GH}/repos/{repo}", headers=self.gh_headers)
        r.raise_for_status()
        d = r.json()
        branch = d["default_branch"]
        c = self.client.get(f"{GH}/repos/{repo}/commits/{branch}", headers=self.gh_headers)
        sha = c.json().get("sha") if c.status_code == 200 else None
        rep = {
            "type": "github", "repo": repo, "revision": sha, "default_branch": branch, "archived": d.get("archived"),
            "license": classify_license((d.get("license") or {}).get("spdx_id")), "stars": d.get("stargazers_count"),
            "pushed_at": d.get("pushed_at"), "size_mb": round(d.get("size", 0) / 1024, 1), "language": d.get("language"),
            "dependencies": [], "warnings": [], "hardware": None,
        }
        if d.get("archived"):
            rep["warnings"].append("Repository is archived (read-only, unmaintained).")
        reqs: list[dict[str, Any]] = []
        for path in ("requirements.txt", "pyproject.toml"):
            f = self.client.get(f"{GH}/repos/{repo}/contents/{path}", params={"ref": sha or branch}, headers=self.gh_headers)
            if f.status_code == 200 and f.json().get("size", 0) < 200_000:
                text = base64.b64decode(f.json()["content"]).decode("utf-8", "replace")
                if path == "requirements.txt":
                    reqs = parse_requirements(text)
                else:
                    reqs = reqs or parse_requirements("\n".join(re.findall(r'"([A-Za-z0-9_.\-\[\]]+\s*[<>=!~][^"]*)"', text)))
                rep["has_" + path.replace(".", "_")] = True
        rep["dependencies"] = reqs
        return self._finish(rep, registry_match=repo)

    def _finish(self, rep: dict[str, Any], registry_match: str) -> dict[str, Any]:
        deps = rep.get("dependencies", [])
        unpinned = [x["name"] for x in deps if not x.get("pinned")]
        if deps and len(unpinned) > len(deps) / 2:
            rep["warnings"].append(f"{len(unpinned)}/{len(deps)} dependencies are not pinned; an install must pin its own lock file.")
        for x in deps:
            if x["name"] in RISKY_DEPS:
                rep["warnings"].append(f"dependency '{x['name']}': {RISKY_DEPS[x['name']]}")
            if x.get("vcs_or_url"):
                rep["warnings"].append("dependency installed from a URL/VCS; pin it to a commit.")
        if rep["license"]["class"] != "permissive":
            rep["warnings"].append(f"Licence class '{rep['license']['class']}': {rep['license']['note']}")
        reg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())["models"]
        hit = next((m for m in reg if (m.get("source") or {}).get("repo", "").lower() == registry_match.lower()), None)
        if hit:
            rep["compatibility"] = {"verdict": "registered", "model_id": hit["id"], "status": hit["status"],
                                    "message": f"Matches registry entry '{hit['id']}' (status: {hit['status']})."}
        else:
            rep["compatibility"] = {"verdict": "custom-adapter-required", "model_id": None, "status": None,
                                    "message": "Not in the registry. It will NOT run automatically: a custom adapter (manifest + "
                                               "entrypoint, reviewed by you) must be written. See docs/CUSTOM_ADAPTERS.md."}
        rep["can_install_automatically"] = bool(hit and hit["status"] not in ("blocked", "candidate"))
        return rep

    def inspect(self, url: str) -> dict[str, Any]:
        p = parse_url(url)
        return self.inspect_hf(p["repo"]) if p["type"] == "huggingface" else self.inspect_github(p["repo"])
