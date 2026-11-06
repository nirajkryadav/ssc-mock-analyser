"""HTTP API for the SSC mock analyser, deployed as a container on Cloud Run.

Two endpoints, deliberately split:

    POST /api/parse     one PDF  -> the question records it contains
    POST /api/analyse   records  -> the full scored report

Splitting them keeps each request small. A full mock is four PDFs totalling
around 40 MB, which exceeds Cloud Run's 32 MB request limit if sent together;
sent one at a time nothing comes close. It also means the browser can show
progress per part, and it keeps the service stateless — no session, no storage,
nothing to clean up.

Nothing is written to disk and nothing is retained between requests. The
uploaded bytes live in memory for the length of one call.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import image_source  # noqa: E402
import parser as sheet_parser  # noqa: E402
import report as report_mod  # noqa: E402
import scoring  # noqa: E402
import topics as topics_mod  # noqa: E402
import records as records_mod  # noqa: E402
from parser import ParseError, Question, SheetMeta  # noqa: E402

log = logging.getLogger("ssc-api")

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 30 * 1024 * 1024))
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:8888,http://127.0.0.1:8888").split(",")
    if o.strip()]

app = FastAPI(title="SSC Mock Analyser API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST", "GET", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

CONFIG = sheet_parser.load_config()
TOPICS = topics_mod.load_topics()
TEMPLATES = report_mod.load_templates()
STORE = records_mod.store_from_env()


# ------------------------------------------------------------------- schemas


class QuestionIn(BaseModel):
    q_no: int
    section: str = ""
    section_name: str = ""
    part: str = ""
    question_text: str = ""
    options: list[str] = Field(default_factory=list)
    selected_index: int | None = None
    correct_index: int | None = None
    status: str
    topic: str | None = None


class MetaIn(BaseModel):
    roll_no: str = ""
    candidate_name: str = ""
    exam_level: str = ""
    test_date: str = ""
    shift: str = ""
    centre: str = ""


class Marking(BaseModel):
    correct: float = 2.0
    wrong: float = -0.5
    skipped: float = 0.0


class AnalyseIn(BaseModel):
    questions: list[QuestionIn]
    meta: MetaIn = Field(default_factory=MetaIn)
    marking: Marking = Field(default_factory=Marking)
    mock_name: str = "Mock 1"
    mock_date: str = ""
    #: The contents of a history.csv the user kept from a previous run.
    history_csv: str | None = None
    #: Store this mock so it appears on the progress page.
    save: bool = False
    source: str = "text"
    ocr_backend: str = ""
    warnings: list[str] = Field(default_factory=list)


# -------------------------------------------------------------------- helpers


def _question_dict(q: Question) -> dict[str, Any]:
    data = q.to_dict()
    data["selected_label"] = q.selected_label
    data["correct_label"] = q.correct_label
    return data


# ------------------------------------------------------------------- endpoints


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Also reports whether OCR is available, which the UI uses to set expectations."""
    return {
        "ok": True,
        "ocr_backends": image_source.available_backends(),
        "max_upload_bytes": MAX_UPLOAD_BYTES,
    }


@app.post("/api/parse")
async def parse(file: UploadFile = File(...)) -> dict[str, Any]:
    """Read one response-sheet PDF and return its question records."""
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"{file.filename} is {len(payload) / 1e6:.1f} MB, over the "
                 f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB limit. Upload one PART at a time.")

    try:
        result = sheet_parser.parse_pdf(payload, CONFIG)
    except ParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # a malformed PDF should not read as a server fault
        log.exception("parse failed for %s", file.filename)
        raise HTTPException(422, f"This PDF could not be read: {exc}") from exc
    finally:
        payload = None  # noqa: F841  — drop the bytes as soon as parsing is done

    topics_mod.tag_questions(result.questions, TOPICS)
    return {
        "filename": file.filename,
        "source": result.source,
        "ocr_backend": result.ocr_backend,
        "warnings": result.warnings,
        "meta": result.meta.__dict__,
        "questions": [_question_dict(q) for q in result.questions],
    }


@app.post("/api/analyse")
def analyse(body: AnalyseIn) -> dict[str, Any]:
    """Score the collected records and build the full report.

    With ``save`` set, the mock is also stored, and its trend is computed
    against every mock already stored rather than against an uploaded CSV.
    """
    if not body.questions:
        raise HTTPException(400, "No questions were supplied.")

    questions = _questions([q.model_dump() for q in body.questions])
    meta = body.meta.model_dump()
    marking = body.marking.model_dump()
    mock_name = body.mock_name.strip() or "Mock 1"
    mock_date = body.mock_date or dt.date.today().isoformat()

    if body.save:
        rid = records_mod.record_id(mock_name)
        others = [r for r in STORE.list() if r.get("id") != rid]
        out = _build(questions, meta, marking, mock_name, mock_date,
                     previous=_history_of(others))
        existing = STORE.get(rid)
        card = scoring.score(questions, {**CONFIG, "marking": marking})
        STORE.put(rid, records_mod.make_record(
            questions=questions, card=card, meta=meta, marking=marking,
            mock_name=mock_name, mock_date=mock_date,
            validation_passed=out["validation"]["passed"], source=body.source,
            ocr_backend=body.ocr_backend, warnings=body.warnings, existing=existing))
        out["record_id"] = rid
        out["replaced_existing"] = existing is not None
        return out

    previous, history_error = None, None
    if body.history_csv:
        try:
            previous = scoring.read_history(io.StringIO(body.history_csv))
        except (scoring.HistoryError, ValueError) as exc:
            history_error = str(exc)
    out = _build(questions, meta, marking, mock_name, mock_date, previous=previous)
    out["history_error"] = history_error
    return out


# --------------------------------------------------------------------- records

_LIST_FIELDS = ("id", "mock_name", "mock_date", "created_at", "updated_at", "summary",
                "sections", "topics", "validation_passed", "source", "marking")


@app.get("/api/records")
def list_records() -> dict[str, Any]:
    """Every stored mock, oldest first, without the per-question detail."""
    return {"records": [{k: r.get(k) for k in _LIST_FIELDS} for r in records_mod.sorted_records(STORE.list())]}


@app.get("/api/records/history.csv")
def export_history() -> Response:
    """All stored mocks as one history.csv, for a backup or a spreadsheet."""
    history = _history_of(STORE.list())
    csv = history.to_csv(index=False) if history is not None else \
        ",".join(scoring.HISTORY_COLUMNS) + "\n"
    return Response(csv, media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="history.csv"'})


@app.get("/api/records/{rid}")
def get_record(rid: str) -> dict[str, Any]:
    """Rebuild the full report for one stored mock."""
    rec = STORE.get(rid)
    if rec is None:
        raise HTTPException(404, "No stored mock with that id.")
    others = [r for r in STORE.list() if r.get("id") != rid]
    out = _build(_questions(rec["questions"]), rec.get("meta") or {},
                 rec.get("marking") or Marking().model_dump(), rec["mock_name"],
                 rec["mock_date"], previous=_history_of(others))
    out.update(record_id=rid, source=rec.get("source", "text"),
               ocr_backend=rec.get("ocr_backend", ""), warnings=rec.get("warnings", []))
    return out


@app.delete("/api/records/{rid}")
def delete_record(rid: str) -> dict[str, Any]:
    if not STORE.delete(rid):
        raise HTTPException(404, "No stored mock with that id.")
    return {"deleted": rid}


# ------------------------------------------------------------------ building


def _questions(items: list[dict[str, Any]]) -> list[Question]:
    return records_mod.questions_from(items, TOPICS)


def _history_of(records: list[dict[str, Any]]):
    return records_mod.history_of(records, TOPICS)


def _build(questions: list[Question], meta: dict[str, Any], marking: dict[str, float],
           mock_name: str, mock_date: str, previous=None) -> dict[str, Any]:
    config = {**CONFIG, "marking": marking}
    sheet_meta = SheetMeta(**{k: (meta.get(k) or "") for k in SheetMeta.__dataclass_fields__})
    result = sheet_parser.ParseResult(questions=questions, meta=sheet_meta)
    problems = sheet_parser.validate(result, config, strict=False)

    card = scoring.score(questions, config)
    current = scoring.questions_frame(questions, mock_date, mock_name)
    history = scoring.merge_history(previous, current)

    rep = report_mod.build(questions, card, config, sheet_meta, mock_name,
                           history=history, templates=TEMPLATES)
    mocks = scoring.mock_trend(history, config["marking"])

    return {
        "validation": {"passed": not problems, "problems": problems},
        "history_error": None,
        "mock_name": mock_name,
        "mock_date": mock_date,
        "meta": sheet_meta.__dict__,
        "summary": records_mod.breakdown(card.overall),
        "marking": config["marking"],
        "sections": [records_mod.breakdown(b) for b in card.sections],
        "topics": [{**records_mod.breakdown(b), "section": card.topic_sections.get(b.label, "")}
                   for b in card.topics],
        "weak": [{"topic": w.topic, "section": w.section, "reason": w.reason,
                  "questions": w.questions, "correct": w.correct,
                  "skipped": w.skipped, "accuracy": round(w.accuracy * 100, 1),
                  "marks_lost": w.marks_lost} for w in rep.weak],
        "unclassified": rep.unclassified,
        "advice": {"text": rep.advice.text,
                   "below_target": rep.advice.below_target,
                   "expected_total": round(rep.advice.expected_total, 1),
                   "per_question": round(rep.advice.per_question, 2)},
        "trend": {"available": rep.trend.available, "text": rep.trend.text,
                  "score_change": rep.trend.score_change,
                  "accuracy_change": rep.trend.accuracy_change,
                  "improved": rep.trend.improved_topics,
                  "declined": rep.trend.declined_topics},
        "mocks": mocks.to_dict(orient="records"),
        "plan": [{"day": d.day, "focus": d.focus,
                  "blocks": [{"title": t, "body": b} for t, b in d.blocks]}
                 for d in rep.plan],
        "wrong_answers": rep.wrong.to_dict(orient="records"),
        "skipped_answers": rep.skipped.to_dict(orient="records"),
        "report_markdown": report_mod.to_markdown(rep, TEMPLATES),
        "history_csv": history.to_csv(index=False),
        "unmatched_csv": topics_mod.unmatched_csv(questions),
    }
