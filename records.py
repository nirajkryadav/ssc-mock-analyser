"""Saved mocks: where they are kept, and how they turn back into a report.

A record is one analysed mock: its question records (so the full report can be
rebuilt later, against the current topics.yaml), the marking it was scored
under, and a precomputed summary so the progress view does not re-score
everything on every load. The original PDFs are kept beside the record (see
put_files) so a past mock can be reopened as it was uploaded.

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


#: A mock's original PDFs, as (file name, bytes) pairs in upload order.
Files = list[tuple[str, bytes]]

#: Firestore caps a document at 1 MiB, so PDFs are stored in chunks below that.
CHUNK = 900_000


class RecordStore(Protocol):
    kind: str

    def list(self) -> list[dict[str, Any]]: ...
    def get(self, rid: str) -> dict[str, Any] | None: ...
    def put(self, rid: str, record: dict[str, Any]) -> None: ...
    def delete(self, rid: str) -> bool: ...
    def put_files(self, rid: str, files: Files) -> None: ...
    def get_files(self, rid: str) -> Files: ...


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")
    return stem[:100] or "sheet.pdf"


class MemoryStore:
    kind = "memory"

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._files: dict[str, Files] = {}

    def list(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._data.values()]

    def get(self, rid: str) -> dict[str, Any] | None:
        r = self._data.get(rid)
        return dict(r) if r else None

    def put(self, rid: str, record: dict[str, Any]) -> None:
        self._data[rid] = dict(record)

    def delete(self, rid: str) -> bool:
        self._files.pop(rid, None)
        return self._data.pop(rid, None) is not None

    def put_files(self, rid: str, files: Files) -> None:
        """Replace the mock's stored PDFs."""
        self._files[rid] = [(n, bytes(b)) for n, b in files]

    def get_files(self, rid: str) -> Files:
        return list(self._files.get(rid, []))


class FileStore(MemoryStore):
    """MemoryStore written through to a JSON file; PDFs go in a folder beside it."""

    kind = "file"

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.files_dir = self.path.parent / "files"
        self._lock = threading.Lock()
        if self.path.exists():
            self._data = json.loads(self.path.read_text("utf-8") or "{}")

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False), "utf-8")
        tmp.replace(self.path)

    def _dir(self, rid: str) -> Path:
        return self.files_dir / record_id(rid)

    def put(self, rid: str, record: dict[str, Any]) -> None:
        with self._lock:
            super().put(rid, record)
            self._flush()

    def delete(self, rid: str) -> bool:
        with self._lock:
            self._clear_files(rid)
            found = super().delete(rid)
            if found:
                self._flush()
            return found

    def _clear_files(self, rid: str) -> None:
        d = self._dir(rid)
        if d.is_dir():
            for f in d.iterdir():
                f.unlink()
            d.rmdir()

    def put_files(self, rid: str, files: Files) -> None:
        with self._lock:
            self._clear_files(rid)
            d = self._dir(rid)
            d.mkdir(parents=True, exist_ok=True)
            for i, (name, data) in enumerate(files):
                (d / f"{i:02d}-{_safe_name(name)}").write_bytes(data)

    def get_files(self, rid: str) -> Files:
        d = self._dir(rid)
        if not d.is_dir():
            return []
        return [(f.name.split("-", 1)[1], f.read_bytes()) for f in sorted(d.iterdir())]


class FirestoreStore:
    """Mocks in collection `mocks`; each mock's PDFs in its `files` subcollection,
    split into chunks under Firestore's 1 MiB document limit."""

    kind = "firestore"

    def __init__(self, project: str | None = None, credentials: Any = None) -> None:
        from google.cloud import firestore  # only needed when this store is used

        self.project = project
        self._client = firestore.Client(project=project, credentials=credentials)
        self._col = self._client.collection(COLLECTION)

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
        self._clear_files(rid)  # a document's subcollection outlives it otherwise
        if not ref.get().exists:
            return False
        ref.delete()
        return True

    def _files(self, rid: str):
        return self._col.document(rid).collection("files")

    def _clear_files(self, rid: str) -> None:
        refs = [d.reference for d in self._files(rid).stream()]
        for start in range(0, len(refs), 400):
            batch = self._client.batch()
            for r in refs[start:start + 400]:
                batch.delete(r)
            batch.commit()

    def put_files(self, rid: str, files: Files) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self._clear_files(rid)
        col = self._files(rid)
        writes = [(f"{i:02d}-{c:04d}", {"file": i, "name": _safe_name(name), "chunk": c,
                                         "data": data[start:start + CHUNK]})
                  for i, (name, data) in enumerate(files)
                  for c, start in enumerate(range(0, max(len(data), 1), CHUNK))]
        # One write per chunk, several in flight: a scanned sheet is a few MB, and
        # sequential round trips to the database region add up.
        with ThreadPoolExecutor(min(6, len(writes)) or 1) as pool:
            list(pool.map(lambda w: col.document(w[0]).set(w[1]), writes))

    def get_files(self, rid: str) -> Files:
        parts = sorted((d.to_dict() for d in self._files(rid).stream()),
                       key=lambda p: (p["file"], p["chunk"]))
        out: Files = []
        for p in parts:
            if not out or len(out) - 1 != p["file"]:
                out.append((p["name"], b""))
            out[-1] = (out[-1][0], out[-1][1] + bytes(p["data"]))
        return out


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
                existing: dict[str, Any] | None = None,
                files: Files | None = None,
                seconds: float | None = None) -> dict[str, Any]:
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
        # What was kept of the original PDFs (the bytes live in the store's files).
        "files": [{"name": _safe_name(n), "size": len(b)} for n, b in (files or [])],
        "analysis_seconds": round(seconds, 1) if seconds is not None else None,
    }
