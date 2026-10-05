"""LLM adapters used for the script and scene plan."""
from __future__ import annotations

import json
import random
import re
from typing import Any

import httpx

from ..config import get_settings
from .base import LLMAdapter, NotConfigured

SYSTEM_WRITER = (
    "You write natural, warm, funny, unscripted-sounding two-host podcast dialogue for a local community "
    "show. Hosts interrupt each other, react, use contractions and short sentences. No stage directions, "
    "no emojis, no markdown. Never invent statistics, real people's quotes, or claims about real events; "
    "keep anecdotes clearly general or fictional. Return ONLY JSON."
)


class LLMBase(LLMAdapter):
    """Shared prompting. Subclasses implement complete()."""

    def write_outline(self, brief: dict[str, Any]) -> list[dict[str, Any]]:
        segs = max(1, round(brief["duration_s"] / 300))
        user = (
            f"Plan a {brief['duration_s'] // 60}-minute episode as {segs} segments. Topic/brief: {brief['topic']}\n"
            f"Hosts: {json.dumps(brief['hosts'])}. Show: {brief['show_name']}.\n"
            'Return JSON: {"segments":[{"title":str,"beats":[str,...],"seconds":int}]} '
            f"with segment seconds summing to about {brief['duration_s']}."
        )
        data = _loads(self.complete(SYSTEM_WRITER, user, json_mode=True))
        return data["segments"]

    def write_segment(self, brief: dict[str, Any], seg: dict[str, Any], prev_tail: list[dict[str, str]],
                      index: int, total: int) -> list[dict[str, str]]:
        words = int(seg["seconds"] * 2.5 * brief.get("pace", 1.0))  # ~150 spoken words/min, tuned by pace
        names = [h["name"] for h in brief["hosts"]]
        user = (
            f"Segment {index + 1}/{total}: {seg['title']}. Beats: {seg['beats']}. Target about {words} words "
            f"total, each line at most 45 words. Speakers: {names}. "
            f"{'Open the episode with a welcome that names the show: ' + brief['show_name'] + '.' if index == 0 else 'Continue naturally from the previous lines.'} "
            f"{'End the episode with a warm sign-off.' if index == total - 1 else 'Do not wrap up the episode yet.'}\n"
            f"Previous lines: {json.dumps(prev_tail[-4:])}\n"
            'Return JSON: {"lines":[{"speaker":str,"text":str}]}'
        )
        data = _loads(self.complete(SYSTEM_WRITER, user, json_mode=True, max_tokens=8000))
        return data["lines"]


def _loads(text: str) -> Any:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


class TemplateLLM(LLMBase):
    """Deterministic placeholder for the credential-free proof. Short episodes only."""
    id = "template-local"
    MAX_SECONDS = 60

    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 4096) -> str:
        raise NotConfigured("template-local does not answer free-form prompts")

    def write_outline(self, brief):
        if brief["duration_s"] > self.MAX_SECONDS:
            raise NotConfigured(
                f"template-local is limited to {self.MAX_SECONDS}s episodes; configure a real LLM "
                "(openai-compatible or anthropic-api) for longer shows")
        return [{"title": "Welcome and a quick chat", "beats": [], "seconds": brief["duration_s"]}]

    def write_segment(self, brief, seg, prev_tail, index, total):
        a, b = brief["hosts"][0]["name"], brief["hosts"][1]["name"]
        topic = brief["topic"].rstrip(".")
        show = brief["show_name"]
        rng = random.Random(f"{brief['seed']}:{topic}")
        opening = [
            (a, f"Welcome back to {show}! I'm {a}."),
            (b, f"And I'm {b}. It's so good to be here with our neighbours again."),
        ]
        middle = [
            (a, f"So {b}, today we're keeping it simple and talking about {topic}."),
            (b, "I love that. Honestly, the best conversations start when people just show up."),
            (a, "Right? Everybody has a story. Most of them never get told."),
            (b, "That's why we're doing this. Real people, real talk, no script."),
            (a, "And I'll be honest, I used to think nobody wanted to hear from regular neighbours."),
            (b, "Oh, and then you found out the opposite is true."),
            (a, "Completely. People are hungry for something that feels local and real."),
            (b, "It's the little things, you know? A name you recognize. A street you've walked down."),
            (a, "Or a coffee line where somebody turns around and says, hey, I know you."),
            (b, "That happens to me all the time. I try to act cool about it."),
            (a, "You do not act cool about it."),
            (b, "No. No, I do not. I wave way too enthusiastically."),
            (a, "Which is exactly why people like you."),
            (b, "Okay, that's the nicest thing you've said to me this week."),
            (a, "It's only Tuesday. Give it time."),
            (b, "Alright, let's talk about what's actually going on around here."),
            (a, "Good idea. Where do we start?"),
            (b, "With whatever you're most curious about. I'll follow your lead."),
        ]
        head, tail = middle[:4], middle[4:]
        rng.shuffle(tail)
        middle = head + tail
        closing = [(b, "That's all the time we have for now."), (a, "Thanks for listening. Take care of each other.")]
        budget = int(seg["seconds"] * 2.8 * brief.get("pace", 1.0))  # Kokoro: ~2.6-2.9 words/s incl. pauses; pace corrects
        lines, words = [], 0
        for spk, txt in opening + closing:
            words += len(txt.split())
        for spk, txt in middle:
            n = len(txt.split())
            if words + n > budget:
                break
            lines.append((spk, txt))
            words += n
        full = opening + lines + closing
        return [{"speaker": s, "text": t} for s, t in full]


class OpenAICompatLLM(LLMBase):
    id = "openai-compatible"
    paid = True  # hosted APIs bill per token; self-hosted endpoints may not

    def __init__(self, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        if not (s.llm_base_url and s.llm_model):
            raise NotConfigured("set LLM_BASE_URL and LLM_MODEL (and LLM_API_KEY if the endpoint needs one)")
        self.base, self.model, self.key = s.llm_base_url.rstrip("/"), s.llm_model, s.llm_api_key
        self.client = httpx.Client(timeout=300, transport=transport)

    def complete(self, system, user, *, json_mode=False, max_tokens=4096):
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "temperature": 0.8,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        r = self.client.post(f"{self.base}/chat/completions", json=body, headers=headers)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class AnthropicLLM(LLMBase):
    id = "anthropic-api"
    paid = True

    def __init__(self, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        if not (s.anthropic_api_key and s.llm_model):
            raise NotConfigured("set ANTHROPIC_API_KEY and LLM_MODEL")
        self.key, self.model = s.anthropic_api_key, s.llm_model
        self.base = (s.llm_base_url or "https://api.anthropic.com").rstrip("/")
        self.client = httpx.Client(timeout=300, transport=transport)

    def complete(self, system, user, *, json_mode=False, max_tokens=4096):
        r = self.client.post(
            f"{self.base}/v1/messages",
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"},
            json={"model": self.model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"])


def make_llm(model_id: str) -> LLMBase:
    return {"template-local": TemplateLLM, "openai-compatible": OpenAICompatLLM, "anthropic-api": AnthropicLLM}[model_id]()
