import base64
import json

import httpx
import pytest

from app import installer
from app.research import Research, classify_license, estimate_vram_gb, parse_requirements, parse_url


def test_parse_url():
    assert parse_url("https://github.com/MeiGen-AI/InfiniteTalk.git") == {"type": "github", "repo": "MeiGen-AI/InfiniteTalk"}
    assert parse_url("https://huggingface.co/Wan-AI/Wan2.2-S2V-14B/tree/main")["repo"] == "Wan-AI/Wan2.2-S2V-14B"
    for bad in ("https://evil.example/a/b", "file:///etc/passwd", "https://huggingface.co/datasets/a/b", "https://github.com/onlyowner"):
        with pytest.raises(ValueError):
            parse_url(bad)


def test_license_classes():
    assert classify_license("apache-2.0")["commercial_use"] is True
    assert classify_license("cc-by-nc-4.0")["class"] == "non-commercial"
    assert classify_license("openrail++")["class"] == "open-weight-restricted"
    assert classify_license(None)["class"] == "unknown"
    assert classify_license("weird-custom")["commercial_use"] is None


def test_requirements_parsing():
    d = parse_requirements("torch==2.4.1\nnumpy>=1.2\n# c\ninsightface\ngit+https://x/y.git@main\n-r other.txt\n")
    names = {x["name"]: x for x in d}
    assert names["torch"]["pinned"] and not names["numpy"]["pinned"] and any(x.get("vcs_or_url") for x in d)


def test_vram_estimate_is_labelled_as_estimate():
    e = estimate_vram_gb(49)
    assert e["estimate_gb"] > 49 and "verify" in e["basis"]


def _hf_transport():
    def h(req: httpx.Request):
        if req.url.path.endswith("/api/models/acme/talker"):
            return httpx.Response(200, json={"sha": "a" * 40, "gated": "auto", "cardData": {"license": "cc-by-nc-4.0"}, "tags": [],
                                             "downloads": 5, "siblings": [{"rfilename": "m.safetensors", "size": 30e9}, {"rfilename": "m.gguf", "size": 30e9},
                                                                         {"rfilename": "run.py", "size": 100}]})
        if req.url.path.endswith("/api/models/Wan-AI/Wan2.2-S2V-14B"):
            return httpx.Response(200, json={"sha": "b" * 40, "cardData": {"license": "apache-2.0"}, "tags": [], "siblings": [{"rfilename": "x.safetensors", "size": 49e9}]})
        if req.url.path == "/api/models":
            return httpx.Response(200, json=[{"id": "a/b", "downloads": 9, "likes": 1, "pipeline_tag": "text-to-speech"}])
        return httpx.Response(404)
    return httpx.MockTransport(h)


def test_inspect_hf_unknown_repo_requires_custom_adapter_and_warns():
    rep = Research(_hf_transport()).inspect("https://huggingface.co/acme/talker")
    assert rep["license"]["class"] == "non-commercial" and rep["gated"] == "auto"
    assert rep["compatibility"]["verdict"] == "custom-adapter-required" and rep["can_install_automatically"] is False
    assert any("Gated" in w for w in rep["warnings"]) and any("Licence class" in w for w in rep["warnings"])
    assert any("allow_patterns" in w for w in rep["warnings"]) and rep["contains_python_files"]


def test_inspect_hf_registered_model():
    rep = Research(_hf_transport()).inspect("https://huggingface.co/Wan-AI/Wan2.2-S2V-14B")
    assert rep["compatibility"]["verdict"] == "registered" and rep["compatibility"]["model_id"] == "wan2.2-s2v-14b"
    assert rep["can_install_automatically"] is False  # candidate: no adapter yet


def test_search_hf():
    assert Research(_hf_transport()).search_hf("tts")[0]["repo"] == "a/b"


def test_inspect_github_with_mock():
    def h(req):
        p = req.url.path
        if p == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": "main", "license": {"spdx_id": "MIT"}, "stargazers_count": 3, "size": 2048, "archived": True, "language": "Python"})
        if p == "/repos/o/r/commits/main":
            return httpx.Response(200, json={"sha": "c" * 40})
        if p == "/repos/o/r/contents/requirements.txt":
            txt = "torch\ninsightface==0.7\nflash-attn\n"
            return httpx.Response(200, json={"size": len(txt), "content": base64.b64encode(txt.encode()).decode()})
        return httpx.Response(404)
    rep = Research(httpx.MockTransport(h)).inspect("https://github.com/o/r")
    assert rep["revision"] == "c" * 40 and rep["license"]["class"] == "permissive"
    ws = " ".join(rep["warnings"])
    assert "archived" in ws and "insightface" in ws and "flash-attn" in ws and "not pinned" in ws
    assert rep["compatibility"]["verdict"] == "custom-adapter-required"


def test_scaffold_and_validate_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "CUSTOM_DIR", tmp_path)
    rep = Research(_hf_transport()).inspect("https://huggingface.co/acme/talker")
    path = installer.scaffold_manifest(rep)
    errs = installer.validate_manifest(path)
    assert any("reviewed_by_owner" in e for e in errs) and any("TODO" in e for e in errs) and any("non-commercial" in e or "blocks" in e for e in errs)
