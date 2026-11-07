"""The SSC CGL syllabus as a checklist, and how far through it you are.

The topics live in syllabus.yaml. Which subtopics are ticked is one small
document in the record store ("syllabus"), so it follows you between devices
exactly like the mocks do.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

SYLLABUS_PATH = Path(__file__).resolve().parent / "syllabus.yaml"
DOC_NAME = "syllabus"


@dataclass
class Topic:
    section: str
    name: str
    subtopics: list[str]
    mocks: list[str] = field(default_factory=list)

    def ids(self) -> list[str]:
        return [item_id(self.section, self.name, s) for s in self.subtopics]


def item_id(section: str, topic: str, subtopic: str) -> str:
    return f"{section}/{topic}/{subtopic}"


def load_syllabus(path: Path = SYLLABUS_PATH) -> dict[str, list[Topic]]:
    """Section key -> its topics, in file order."""
    raw = yaml.safe_load(Path(path).read_text("utf-8")) or {}
    out: dict[str, list[Topic]] = {}
    for section, topics in raw.items():
        out[section] = []
        for name, spec in (topics or {}).items():
            spec = spec or {}
            out[section].append(Topic(section=section, name=str(name),
                                      subtopics=[str(s) for s in spec.get("subtopics", [])],
                                      mocks=[str(m) for m in spec.get("mocks", [name])]))
    return out


def done_set(doc: dict[str, Any] | None) -> set[str]:
    return set((doc or {}).get("done", []))


def count(topics: Iterable[Topic], done: set[str]) -> tuple[int, int]:
    """(ticked, total) subtopics across these topics."""
    ids = [i for t in topics for i in t.ids()]
    return sum(1 for i in ids if i in done), len(ids)


def mock_stats(records: Iterable[dict[str, Any]], section_keys: dict[str, str] | None = None
               ) -> dict[tuple[str, str], dict[str, float]]:
    """(section key, mock topic label) -> totals across every saved mock.

    Saved mocks name a topic's section in full ("Quantitative Aptitude");
    `section_keys` maps those names to the keys used here ("quant").
    """
    keys = section_keys or {}
    stats: dict[tuple[str, str], dict[str, float]] = {}
    for rec in records:
        for t in rec.get("topics", []):
            section = t.get("section", "")
            cur = stats.setdefault((keys.get(section, section), t["label"]), {
                "questions": 0, "correct": 0, "wrong": 0, "skipped": 0,
                "marks_lost": 0.0, "mocks": 0})
            for k in ("correct", "wrong", "skipped", "marks_lost"):
                cur[k] += t.get(k, 0)
            cur["questions"] += t.get("total", 0)
            cur["mocks"] += 1
    return stats


def topic_mock_stats(topic: Topic, stats: dict[tuple[str, str], dict[str, float]]
                     ) -> dict[str, float] | None:
    """This topic's mock results, summed over the topic labels it maps to."""
    parts = [stats[(topic.section, m)] for m in topic.mocks if (topic.section, m) in stats]
    if not parts:
        return None
    out = {k: sum(p[k] for p in parts) for k in ("questions", "correct", "wrong",
                                                  "skipped", "marks_lost")}
    attempted = out["correct"] + out["wrong"]
    out["accuracy"] = out["correct"] / attempted if attempted else 0.0
    out["attempted"] = attempted
    return out


def is_weak(st: dict[str, float] | None, accuracy: float = 0.6, skip_rate: float = 0.5,
            min_questions: int = 2) -> bool:
    """Same rule the mock report uses for "weak areas"."""
    if not st or st["questions"] < min_questions:
        return False
    return bool((st["attempted"] and st["accuracy"] < accuracy)
                or st["skipped"] / st["questions"] > skip_rate)
