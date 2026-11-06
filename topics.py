"""Assign a topic to each question by keyword match.

The dictionaries live in topics.yaml, keyed by section. The first keyword that
matches wins, so the YAML is ordered specific-first. Nothing here is clever: it
is a lookup table that a user can improve by editing one file, and every question
it fails to place is written to unmatched.csv so the gaps are visible.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

TOPICS_PATH = Path(__file__).with_name("topics.yaml")
UNMATCHED = "Unclassified"


def compile_keyword(keyword: str) -> re.Pattern[str]:
    """Match a keyword on word boundaries, tolerant of whitespace runs.

    Plain substring matching is too loose for the short keywords this domain
    needs: "tan" hides inside "simultaneous", "sec" inside "second" and "sin"
    inside "using". Boundaries are added only where the keyword's own edge is a
    word character, so symbols such as "%" still match, and a trailing plural is
    allowed so "simultaneous equation" still finds "simultaneous equations".
    """
    text = str(keyword).strip().lower()
    body = r"\s+".join(re.escape(part) for part in text.split())
    left = r"\b" if text[:1].isalnum() else ""
    right = r"(?:e?s)?\b" if text[-1:].isalpha() else ""
    return re.compile(f"{left}{body}{right}")


def load_topics(path: str | Path | None = None) -> dict[str, dict[str, list[re.Pattern[str]]]]:
    """Read topics.yaml and compile every keyword once."""
    with open(path or TOPICS_PATH, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return {section: {topic: [compile_keyword(k) for k in keywords if str(k).strip()]
                      for topic, keywords in topics.items()}
            for section, topics in raw.items()}


def _haystack(question) -> str:
    parts = [question.question_text or ""]
    parts.extend(question.options or [])
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def match_topic(question, topics: dict[str, dict[str, list[re.Pattern[str]]]]) -> str:
    """The first topic in the section whose keyword appears in the question."""
    text = _haystack(question)
    for topic, patterns in topics.get(question.section, {}).items():
        if any(p.search(text) for p in patterns):
            return topic
    return UNMATCHED


def tag_questions(questions: Sequence[Any],
                  topics: dict[str, dict[str, list[re.Pattern[str]]]] | None = None) -> list[Any]:
    """Set .topic on every question in place and return the list."""
    topics = topics if topics is not None else load_topics()
    for q in questions:
        q.topic = match_topic(q, topics)
    return list(questions)


def unmatched_rows(questions: Iterable[Any]) -> list[dict[str, Any]]:
    return [{"q_no": q.q_no, "section": q.section_name, "status": q.status,
             "question_text": q.question_text, "options": " | ".join(q.options)}
            for q in questions if q.topic == UNMATCHED]


def unmatched_csv(questions: Iterable[Any]) -> str:
    """CSV of every question the dictionaries could not place."""
    rows = unmatched_rows(questions)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=["q_no", "section", "status", "question_text", "options"])
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def write_unmatched(questions: Iterable[Any], path: str | Path = "unmatched.csv") -> int:
    text = unmatched_csv(questions)
    Path(path).write_text(text, encoding="utf-8")
    return text.count("\n") - 1
