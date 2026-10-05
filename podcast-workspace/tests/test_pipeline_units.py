import json
import random

import httpx
import pytest

from app.adapters import llm as llm_mod
from app.adapters.base import Ctx, NotConfigured
from app.pipeline import script as script_mod
from app.pipeline.spec import PodcastSpec, parse_instruction
from app.pipeline.timeline import MAX_CLOSE_S, make_ass, make_srt, plan_shots


def test_parse_instruction_extracts_what_it_can():
    r = parse_instruction("Create a 20-minute realistic podcast with Matt and Chloe, natural voices, lip-sync, body movement, "
                          "three camera angles, captions, and “Durham & Clarington Community REAL TALK” at the bottom left.")
    assert r["overrides"]["duration_s"] == 1200
    assert r["overrides"]["group_name"] == "Durham & Clarington Community REAL TALK"
    assert any("stylised animatic" in n for n in r["notes"])
    assert parse_instruction("make a 45 second show")["overrides"]["duration_s"] == 45
    assert parse_instruction("a 1.5 hour show")["overrides"]["duration_s"] == 5400


def test_default_spec_matches_brief():
    s = PodcastSpec()
    assert [h.name for h in s.hosts] == ["Matt", "Chloe"]
    assert "light brown skin" in s.hosts[0].description and "36" in s.hosts[0].description
    assert "red hair" in s.hosts[1].description and "pale skin" in s.hosts[1].description
    assert s.group_name == "Durham & Clarington Community REAL TALK" and s.cameras == ["wide", "close_0", "close_1"]
    assert s.spoken_group == "Durham and Clarington Community Real Talk"


@pytest.mark.parametrize("seed", range(40))
def test_shot_plan_invariants(seed):
    rnd = random.Random(seed)
    spec = PodcastSpec(duration_s=600)
    t, lines = 0.4, []
    for i in range(rnd.randint(4, 60)):
        d = rnd.uniform(0.6, 25)
        lines.append({"speaker": rnd.choice(["Matt", "Chloe"]), "start": t, "end": t + d})
        t += d + rnd.uniform(0.1, 0.5)
    total = t + 1.2
    shots = plan_shots(spec, lines, total)
    assert shots[0]["t0"] == 0 and shots[-1]["t1"] == round(total, 3) and shots[-1]["camera"] == "wide"
    for a, b in zip(shots, shots[1:]):
        assert abs(a["t1"] - b["t0"]) < 1e-6 and a["camera"] != b["camera"]
    idx = {"Matt": 0, "Chloe": 1}
    for s in shots:
        if s["camera"] != "wide":
            # while a close-up is on screen, only that host may be speaking
            who = int(s["camera"][-1])
            for ln in lines:
                if ln["end"] > s["t0"] + 0.05 and ln["start"] < s["t1"] - 0.05:
                    assert idx[ln["speaker"]] == who, (s, ln)
            assert s["t1"] - s["t0"] <= MAX_CLOSE_S + 0.01
    assert {"wide", "close_0", "close_1"} >= {s["camera"] for s in shots}


def test_captions_cover_every_line_and_are_monotonic():
    spec = PodcastSpec()
    lines = [{"speaker": "Matt", "text": "Welcome back to the show, everybody, it is great to be here today.", "start": 0.5, "end": 5.0},
             {"speaker": "Chloe", "text": "Thanks!", "start": 5.2, "end": 5.9}]
    srt = make_srt(lines)
    assert "Thanks!" in srt and srt.count("-->") >= 2
    ass = make_ass(spec, lines)
    assert "PlayResX: 1280" in ass and ass.count("Dialogue:") == srt.count("-->")


# ------------------------------------------------------------------ LLM adapters against a mock server
def _mock(handler):
    return httpx.MockTransport(handler)


def test_openai_compat_script_flow(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.local/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_API_KEY", "key-should-not-leak-1234")
    from app import config
    config.reset_settings_cache()
    seen = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.append((req.headers.get("authorization"), body))
        if "segments" in body["messages"][1]["content"] and "Plan" in body["messages"][1]["content"]:
            out = {"segments": [{"title": "A", "beats": ["x"], "seconds": 20}, {"title": "B", "beats": ["y"], "seconds": 20}]}
        else:
            out = {"lines": [{"speaker": "matt", "text": "Hello *there* [laughs] friend."}, {"speaker": "Nobody", "text": "ignored"},
                             {"speaker": "Chloe", "text": "Hi (smiling) Matt."}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": "```json\n" + json.dumps(out) + "\n```"}}]})

    llm = llm_mod.OpenAICompatLLM(transport=_mock(handler))
    import tempfile
    from pathlib import Path
    from app import db
    wd = Path(tempfile.mkdtemp())
    db.x("INSERT INTO projects(id,title,instruction,spec_json,created_at,updated_at) VALUES('p','t','i','{}',0,0)")
    ctx = Ctx("j", wd, lambda m: None, lambda: None)
    lines = script_mod.build_script(PodcastSpec(duration_s=40, llm="openai-compatible"), llm, ctx, "p", wd)
    assert [l["speaker"] for l in lines] == ["Matt", "Chloe"] * 2
    assert lines[0]["text"] == "Hello there friend." and lines[1]["text"] == "Hi Matt."
    assert seen[0][0] == "Bearer key-should-not-leak-1234"
    n_calls = len(seen)
    script_mod.build_script(PodcastSpec(duration_s=40, llm="openai-compatible"), llm, ctx, "p", wd)  # resume: all checkpointed
    assert len(seen) == n_calls


def test_anthropic_adapter_request_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0123456789abcdef")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    from app import config
    config.reset_settings_cache()
    captured = {}

    def handler(req):
        captured["h"], captured["b"], captured["u"] = dict(req.headers), json.loads(req.content), str(req.url)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "{\"ok\":1}"}]})

    a = llm_mod.AnthropicLLM(transport=_mock(handler))
    assert a.complete("sys", "hi") == '{"ok":1}'
    assert captured["u"].endswith("/v1/messages") and captured["h"]["x-api-key"].startswith("sk-ant")
    assert captured["b"]["system"] == "sys" and captured["b"]["messages"][0]["content"] == "hi"


def test_adapters_refuse_when_unconfigured():
    with pytest.raises(NotConfigured):
        llm_mod.OpenAICompatLLM()
    with pytest.raises(NotConfigured):
        llm_mod.AnthropicLLM()
    t = llm_mod.TemplateLLM()
    with pytest.raises(NotConfigured):
        t.write_outline({"duration_s": 600})


def test_template_writer_hits_requested_length_roughly():
    t = llm_mod.TemplateLLM()
    brief = {"topic": "local hockey", "duration_s": 30, "seed": 1, "show_name": "Real Talk",
             "hosts": [{"name": "Matt"}, {"name": "Chloe"}]}
    seg = t.write_outline(brief)[0]
    lines = t.write_segment(brief, seg, [], 0, 1)
    words = sum(len(l["text"].split()) for l in lines)
    assert 50 <= words <= 95 and lines[0]["speaker"] == "Matt" and lines[-1]["speaker"] == "Matt"
