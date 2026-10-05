"""Episode specification and a deliberately simple instruction parser.

The parser is regex-based, not an LLM: it extracts the facts it can (length, host names,
quoted on-screen text) and leaves everything else to the editable spec the owner confirms
before anything runs.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

GROUP_NAME = "Durham & Clarington Community REAL TALK"


class Host(BaseModel):
    name: str
    description: str
    voice: str = ""
    side: str = "left"


DEFAULT_HOSTS = [
    Host(name="Matt", side="left", voice="am_michael",
         description="adult man around 36, light brown skin, average build, short dark-brown hair, light stubble, casual jacket"),
    Host(name="Chloe", side="right", voice="af_heart",
         description="adult woman, long red hair, pale skin, calm confident presence, sage-green top"),
]


class PodcastSpec(BaseModel):
    topic: str = "community life in Durham Region and Clarington"
    duration_s: int = Field(30, ge=10, le=3600)
    group_name: str = GROUP_NAME          # shown bottom-left for the whole episode
    hosts: list[Host] = Field(default_factory=lambda: [h.model_copy() for h in DEFAULT_HOSTS])
    cameras: list[str] = ["wide", "close_0", "close_1"]
    captions: bool = True
    width: int = 1280
    height: int = 720
    fps: int = 24
    llm: str = "template-local"
    tts: str = "kokoro-onnx-local"
    visuals: str = "procedural-draft"
    seed: int = 7
    pace: float = Field(1.0, ge=0.4, le=2.5)  # word-target multiplier; the runner tunes it from the measured voice length

    @field_validator("hosts")
    @classmethod
    def two_hosts(cls, v):
        if len(v) != 2:
            raise ValueError("exactly two hosts are supported")
        return v

    @property
    def spoken_group(self) -> str:
        return self.group_name.replace("&", "and").replace("REAL TALK", "Real Talk")

    def uses_cloud(self) -> dict[str, bool]:
        return {"llm": self.llm not in ("template-local",), "tts": self.tts != "kokoro-onnx-local",
                "image": self.visuals != "procedural-draft", "lipsync": self.visuals != "procedural-draft"}

    def is_paid(self) -> bool:
        return any(self.uses_cloud().values())


_DUR = re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b", re.I)
_WITH = re.compile(r"\bwith\s+([A-Z][a-z]+)\s+and\s+([A-Z][a-z]+)")
_QUOTE = re.compile(r"[\"“”']([^\"“”']{3,80})[\"“”']")


def parse_instruction(text: str) -> dict[str, Any]:
    """Return PodcastSpec overrides found in free text, plus notes for the owner."""
    out: dict[str, Any] = {}
    notes: list[str] = []
    m = _DUR.search(text)
    if m:
        n, unit = float(m.group(1)), m.group(2).lower()
        secs = n * (3600 if unit.startswith(("h")) else 60 if unit.startswith("m") else 1)
        out["duration_s"] = int(secs)
    w = _WITH.search(text)
    if w and {w.group(1), w.group(2)} != {"Matt", "Chloe"}:
        notes.append(f"Hosts '{w.group(1)}' and '{w.group(2)}' were mentioned; descriptions must be edited in the spec "
                     "(only Matt and Chloe have built-in character descriptions).")
        out["hosts"] = [
            Host(name=w.group(1), side="left", voice="am_adam", description="adult man").model_dump(),
            Host(name=w.group(2), side="right", voice="af_sarah", description="adult woman").model_dump()]
    q = _QUOTE.findall(text)
    if q and re.search(r"bottom[- ]left|group name|watermark|lower[- ]left", text, re.I):
        out["group_name"] = q[-1].strip()
    a = re.search(r"\babout\s+(.+?)(?:[.,;]|\bwith\b|$)", text, re.I)
    if a:
        out["topic"] = a.group(1).strip()
    if re.search(r"\bno captions?\b", text, re.I):
        out["captions"] = False
    for word, label in (("realistic", "photoreal video"), ("lip-?sync", "lip-sync"), ("natural voices?", "natural voices"),
                        ("body movement", "body movement")):
        if re.search(word, text, re.I):
            notes.append(f"Brief asks for {label}; the free draft renderer is a stylised animatic. "
                         "Realism needs the cloud GPU path, which is not yet verified (docs/STATUS.md).")
            break
    return {"overrides": out, "notes": notes}
