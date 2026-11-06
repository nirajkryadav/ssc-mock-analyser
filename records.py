"""Saved mocks: where they are kept, and how they turn back into a report.

A record is one analysed mock: its question records (so the full report can be
rebuilt later, against the current topics.yaml), the marking it was scored
under, and a precomputed summary so the progress view does not re-score
everything on every load. The PDF itself is never kept.

Three interchangeable stores:

    FirestoreStore  Cloud Firestore. Used when the app is deployed, because
                    Streamlit Community Cloud wipes its disk on every restart.
    FileStore       A JSON file on this machine. The default when running locally.
    MemoryStore     A dict, gone on restart. Used by the tests.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Protocol

import pandas as pd

import scoring
import topics as topics_mod
from parser import Question

ROOT = Path(__file__).resolve().parent
COLLECTION = "mocks"
DEFAULT_FILE = ROOT / ".records" / "records.json"


# ----------------------------------------------------------------------- stores


class RecordStore(Protocol):
    kind: str

    def list(self) -> list[dict[str, Any]]: ...
    def get(self, rid: str) -> dict[str, Any] | None: ...
    def put(self, rid: str, record: dict[str, Any]) -> None: ...
    def delete(self, rid: str) -> bool: ...


class MemoryStore:
    kind = "memory"

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def list(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._data.values()]

    def get(self, rid: str) -> dict[str, Any] | None:
        r = self._data.get(rid)
        return dict(r) if r else None

    def put(self, rid: str, record: dict[str, Any]) -> None:
        self._data[rid] = dict(record)

    def delete(self, rid: str) -> bool:
        return self._data.pop(rid, None) is not None


class FileStore(MemoryStore):
    """MemoryStore written through to a JSON file after every change."""

    kind = "file"

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = Path(path)
        self._lock = threading.Lock()
        if self.path.exists():
            self._data = json.loads(self.path.read_text("utf-8") or "{}")

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False), "utf-8")
        tmp.replace(self.path)

    def put(self, rid: str, record: dict[str, Any]) -> None:
        with self._lock:
            super().put(rid, record)
            self._flush()

    def delete(self, rid: str) -> bool:
        with self._lock:
            found = super().delete(rid)
            if found:
                self._flush()
            return found


class FirestoreStore:
    kind = "firestore"

    def __init__(self, project: str | None = None, credentials: Any = None) -> None:
        from google.cloud import firestore  # only needed when this store is used

        self.project = project
        self._col = firestore.Client(project=project, credentials=credentials) \
            .collection(COLLECTION)

    @classmethod
    def from_service_account(cls, info: dict[str, Any]) -> "FirestoreStore":
        """Connect with a service-account key, never the machine's default login."""
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(dict(info))
        return cls(project=info["project_id"], credentials=creds)

    def list(self) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self._col.stream()]

    def get(self, rid: str) -> dict[str, Any] | None:
        snap = self._col.document(rid).get()
        return snap.to_dict() if snap.exists else None

    def put(self, rid: str, record: dict[str, Any]) -> None:
        self._col.document(rid).set(record)

    def delete(self, rid: str) -> bool:
        ref = self._col.document(rid)
        if not ref.get().exists:
            return False
        ref.delete()
        return True


def store_from_env() -> RecordStore:
    """Pick a store from RECORDS_STORE (firestore | file | memory). Used by the API."""
    kind = os.environ.get("RECORDS_STORE", "file").strip().lower()
    if kind == "firestore":
        return FirestoreStore(os.environ.get("GOOGLE_CLOUD_PROJECT") or None)
    if kind == "memory":
        return MemoryStore()
    return FileStore(Path(os.environ.get("RECORDS_FILE", DEFAULT_FILE)))


# ---------------------------------------------------------------------- records


def record_id(mock_name: str) -> str:
    """Stable id from the mock's name, so re-analysing a mock replaces it."""
    slug = re.sub(r"[^a-z0-9]+", "-", mock_name.strip().lower()).strip("-")
    return slug[:120] or "mock"


def sorted_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Oldest mock first."""
    return sorted(records, key=lambda r: (r.get("mock_date", ""), r.get("created_at", "")))


def breakdown(b: scoring.Breakdown) -> dict[str, Any]:
    return {
        "label": b.label, "total": b.total, "correct": b.correct, "wrong": b.wrong,
        "skipped": b.skipped, "attempted": b.attempted,
        "accuracy": round(b.accuracy * 100, 1),
        "attempt_rate": round(b.attempt_rate * 100, 1),
        "score": round(b.score, 2), "max_score": round(b.max_score, 2),
        "marks_lost": round(b.marks_lost, 2),
    }


def questions_from(items: Iterable[dict[str, Any]], topics: dict[str, Any]) -> list[Question]:
    """Rebuild Question objects from stored or posted dicts, and re-tag them.

    Topics are re-derived rather than trusted: topics.yaml may have moved on
    since the mock was saved, and posted data is user input like any other.
    """
    fields = ("q_no", "section", "section_name", "part", "question_text", "options",
              "selected_index", "correct_index", "status", "topic")
    questions = [Question(**{k: d.get(k) for k in fields})
                 for d in sorted(items, key=lambda d: d["q_no"])]
    for q in questions:
        q.options = list(q.options or [])
        for name in ("section", "section_name", "part", "question_text"):
            setattr(q, name, getattr(q, name) or "")
    topics_mod.tag_questions(questions, topics)
    return questions


def history_of(records: Iterable[dict[str, Any]], topics: dict[str, Any]) -> pd.DataFrame | None:
    """The stored mocks in history.csv shape, or None when there are none."""
    frames = [scoring.questions_frame(questions_from(r["questions"], topics),
                                      r["mock_date"], r["mock_name"])
              for r in records if r.get("questions")]
    return pd.concat(frames, ignore_index=True) if frames else None


def make_record(*, questions: list[Question], card: scoring.ScoreCard, meta: dict[str, Any],
                marking: dict[str, float], mock_name: str, mock_date: str,
                validation_passed: bool, source: str = "text", ocr_backend: str = "",
                warnings: list[str] | None = None,
                existing: dict[str, Any] | None = None) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return {
        "id": record_id(mock_name),
        "mock_name": mock_name,
        "mock_date": mock_date,
        "created_at": (existing or {}).get("created_at", now),
        "updated_at": now,
        "meta": meta,
        "marking": marking,
        "source": source,
        "ocr_backend": ocr_backend or "",
        "warnings": list(warnings or []),
        "questions": [q.to_dict() for q in questions],
        "summary": breakdown(card.overall),
        "sections": [breakdown(b) for b in card.sections],
        "topics": [{**breakdown(b), "section": card.topic_sections.get(b.label, "")}
                   for b in card.topics],
        "validation_passed": validation_passed,
    }
