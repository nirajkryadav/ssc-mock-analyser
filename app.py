"""Streamlit app for the SSC mock analyser: one page to analyse a mock, follow
your progress across every saved mock, and reopen any of them.

Where saved mocks go:
  * Firestore, when Streamlit secrets hold a [firebase] service-account key.
    This is how the deployed app keeps them: Streamlit Community Cloud wipes its
    disk whenever the app restarts or sleeps.
  * Otherwise .records/records.json on this machine.

The uploaded PDF is read in memory and dropped. What is kept is the scored
question records, so a past mock's full report can be rebuilt.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

import image_source
import parser as sheet_parser
import records as records_mod
import report as report_mod
import scoring
import topics as topics_mod
from parser import ParseError, SheetMeta

st.set_page_config(page_title="SSC Mock Analyser", page_icon="📝", layout="wide")

BLUE = "#3987e5"


def _ink() -> str:
    """Label colour for chart text: Vega-Lite does not follow Streamlit's theme."""
    try:
        return "#f0efec" if st.context.theme.type == "dark" else "#0b0b0b"
    except Exception:
        return "#52514e"
SHORT = {
    "General Intelligence and Reasoning": "Reasoning",
    "General Awareness": "General Awareness",
    "Quantitative Aptitude": "Quant",
    "English Comprehension": "English",
}


@st.cache_resource
def _config():
    return sheet_parser.load_config()


@st.cache_resource
def _topics():
    return topics_mod.load_topics()


@st.cache_resource
def _templates():
    return report_mod.load_templates()


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _num(x: float) -> str:
    return f"{round(float(x), 2):g}"


# ------------------------------------------------------------------------ storage


def _firebase_key() -> dict[str, Any] | None:
    """A service-account key from Streamlit secrets, or from FIREBASE_KEY_FILE."""
    try:
        if "firebase" in st.secrets:
            return dict(st.secrets["firebase"])
    except Exception:  # no secrets.toml at all is the normal local case
        pass
    path = os.environ.get("FIREBASE_KEY_FILE")
    if path and Path(path).is_file():
        return json.loads(Path(path).read_text("utf-8"))
    return None


def _store_key() -> str:
    key = _firebase_key()
    if key:
        return f"firestore:{key.get('project_id', '')}"
    return f"file:{os.environ.get('RECORDS_FILE', records_mod.DEFAULT_FILE)}"


@st.cache_resource(show_spinner=False)
def _store(store_key: str) -> records_mod.RecordStore:
    if store_key.startswith("firestore:"):
        return records_mod.FirestoreStore.from_service_account(_firebase_key())
    return records_mod.FileStore(Path(store_key.split(":", 1)[1]))


@st.cache_data(ttl=300, show_spinner="Loading your mocks…")
def _load_records(store_key: str) -> list[dict[str, Any]]:
    return records_mod.sorted_records(_store(store_key).list())


def _refresh() -> None:
    _load_records.clear()


# ------------------------------------------------------------------- report build


def _report_for(rec: dict[str, Any], all_records: list[dict[str, Any]]):
    """Rebuild a saved mock's full report, compared against every other saved mock."""
    base = _config()
    config = {**base, "marking": rec.get("marking") or base["marking"]}
    questions = records_mod.questions_from(rec["questions"], _topics())
    stored_meta = rec.get("meta") or {}
    meta = SheetMeta(**{k: stored_meta.get(k) or "" for k in SheetMeta.__dataclass_fields__})
    card = scoring.score(questions, config)

    others = [r for r in all_records if r.get("id") != rec.get("id")]
    current = scoring.questions_frame(questions, rec["mock_date"], rec["mock_name"])
    history = scoring.merge_history(records_mod.history_of(others, _topics()), current)

    parsed = sheet_parser.ParseResult(questions=questions, meta=meta)
    problems = sheet_parser.validate(parsed, config, strict=False)
    rep = report_mod.build(questions, card, config, meta, rec["mock_name"],
                           history=history, templates=_templates())
    validation = sheet_parser.validation_report(parsed, config)
    return rep, card, questions, config, problems, validation


# ===================================================================== the page

st.session_state.setdefault("upload_round", 0)
store_key = _store_key()

st.title("SSC Mock Analyser")
if store_key.startswith("firestore:"):
    st.caption("Upload a response sheet, follow your progress across every mock, and "
               "reopen any past one. Mocks are saved to your Firebase database.")
else:
    st.caption("Upload a response sheet, follow your progress across every mock, and "
               "reopen any past one. Mocks are saved on this computer "
               "(.records/records.json).")

try:
    records = _load_records(store_key)
except Exception as exc:  # a bad key or an unreachable database should say so
    st.error(f"**Could not load your saved mocks.**\n\n{exc}")
    records = []

flash = st.session_state.pop("flash", None)
if note := st.session_state.pop("flash_top", None):
    st.success(note)


# ------------------------------------------------------------------- 1. progress

st.header("Your progress")

if not records:
    st.info("**No mocks saved yet.** Analyse your first response sheet below. Every "
            "mock you analyse is saved, and this section fills in as you go.")
else:
    last = records[-1]
    prev = records[-2] if len(records) > 1 else None
    best = max(records, key=lambda r: r["summary"]["score"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Latest score", _num(last["summary"]["score"]),
        f"{last['summary']['score'] - prev['summary']['score']:+g} vs {prev['mock_name']}"
        if prev else None,
        help=f"{last['mock_name']}, out of {_num(last['summary']['max_score'])}")
    c2.metric("Best score", _num(best["summary"]["score"]),
              help=f"{best['mock_name']} · {best['mock_date']}")
    c3.metric(
        "Latest accuracy", f"{last['summary']['accuracy']:.1f}%",
        f"{last['summary']['accuracy'] - prev['summary']['accuracy']:+.1f} pts"
        if prev else None,
        help="Of the questions attempted, the share answered correctly.")
    c4.metric("Mocks analysed", len(records),
              help=f"{records[0]['mock_date']} to {last['mock_date']}")

    names = [r["mock_name"] for r in records]
    scores = pd.DataFrame([{
        "Mock": r["mock_name"], "Date": r["mock_date"],
        "Score": r["summary"]["score"], "Out of": r["summary"]["max_score"],
        "Accuracy %": r["summary"]["accuracy"],
        "Attempted %": r["summary"]["attempt_rate"],
    } for r in records])
    max_score = float(scores["Out of"].max() or 200)

    st.subheader("Score by mock")
    x = alt.X("Mock:N", sort=names, axis=alt.Axis(labelAngle=0, title=None))
    y = alt.Y("Score:Q", scale=alt.Scale(domain=[0, max_score]), title=None)
    tip = ["Mock", "Date", "Score", "Out of", "Accuracy %", "Attempted %"]
    base = alt.Chart(scores).encode(x=x, y=y, tooltip=tip)
    score_chart = (
        base.mark_area(color=BLUE, opacity=0.1)
        + base.mark_line(color=BLUE, strokeWidth=2)
        + base.mark_point(color=BLUE, filled=True, size=70, opacity=1)
        + alt.Chart(scores.tail(1)).mark_text(align="left", dx=9, fontWeight="bold",
                                              color=_ink())
        .encode(x=x, y=y, text=alt.Text("Score:Q", format="~g"))
    ).properties(height=260)
    st.altair_chart(score_chart, width="stretch")

    st.subheader("Accuracy by section")
    st.caption("Of the questions you attempted, the share you got right.")
    section_names = [s["label"] for s in last["sections"]]
    cols = st.columns(len(section_names) or 1)
    for col, name in zip(cols, section_names):
        rows = []
        for r in records:
            s = next((s for s in r.get("sections", []) if s["label"] == name), None)
            if s:
                rows.append({"Mock": r["mock_name"], "Accuracy %": s["accuracy"],
                             "Score": s["score"], "Correct": s["correct"],
                             "Wrong": s["wrong"], "Skipped": s["skipped"]})
        df = pd.DataFrame(rows)
        sx = alt.X("Mock:N", sort=names, axis=alt.Axis(labels=False, ticks=False, title=None))
        sy = alt.Y("Accuracy %:Q", scale=alt.Scale(domain=[0, 100]),
                   axis=alt.Axis(values=[0, 50, 100], title=None))
        sbase = alt.Chart(df).encode(x=sx, y=sy, tooltip=list(df.columns))
        chart = (
            sbase.mark_line(color=BLUE, strokeWidth=2)
            + sbase.mark_point(color=BLUE, filled=True, size=40, opacity=1)
            + alt.Chart(df.tail(1)).mark_text(align="left", dx=7, fontWeight="bold",
                                          color=_ink())
            .encode(x=sx, y=sy, text=alt.Text("Accuracy %:Q", format=".1f"))
        ).properties(height=140, title=SHORT.get(name, name))
        col.altair_chart(chart, width="stretch")

    st.subheader("Topics that keep costing marks")
    lost: dict[str, dict[str, Any]] = {}
    for r in records:
        for t in r.get("topics", []):
            if t["label"] == "Unclassified":
                continue
            cur = lost.setdefault(t["label"], {"Topic": t["label"],
                                               "Section": SHORT.get(t["section"], t["section"]),
                                               "Marks lost": 0.0, "Wrong": 0, "Skipped": 0,
                                               "Mocks with losses": 0})
            cur["Marks lost"] += t["marks_lost"]
            cur["Wrong"] += t["wrong"]
            cur["Skipped"] += t["skipped"]
            cur["Mocks with losses"] += 1 if t["marks_lost"] > 0 else 0
    top = sorted((v for v in lost.values() if v["Marks lost"] > 0),
                 key=lambda v: -v["Marks lost"])[:8]
    if len(records) > 1:
        st.caption(f"Marks lost per topic, added up over all {len(records)} mocks. "
                   "The top of this list is where revision pays best.")
    else:
        st.caption("Marks lost per topic in this mock. Over several mocks this shows "
                   "which gaps keep coming back.")
    if top:
        tdf = pd.DataFrame(top)
        tdf["Marks lost"] = tdf["Marks lost"].round(2)
        ty = alt.Y("Topic:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220))
        tx = alt.X("Marks lost:Q", title=None, axis=alt.Axis(tickCount=5))
        tbase = alt.Chart(tdf).encode(y=ty, x=tx, tooltip=list(tdf.columns))
        bars = (
            tbase.mark_bar(color=BLUE, size=16, cornerRadiusEnd=4)
            + tbase.mark_text(align="left", dx=5, color=_ink())
            .encode(text=alt.Text("Marks lost:Q", format="~g"))
        ).properties(height=36 * len(tdf) + 10)
        st.altair_chart(bars, width="stretch")
    else:
        st.success("No marks lost to any tagged topic.")


# -------------------------------------------------------------------- 2. analyse

st.header("Analyse a new mock")
config = _config()
round_ = st.session_state["upload_round"]

with st.container(border=True):
    uploads = st.file_uploader(
        "Response sheet PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        key=f"uploads_{round_}",
        help="SSC serves one PDF per PART, so upload all four, or a single combined "
             "sheet if you have one.",
    )
    c1, c2 = st.columns(2)
    mock_name = c1.text_input("Mock name", value=f"Mock {len(records) + 1}",
                              key=f"mock_name_{round_}_{len(records)}")
    mock_date = c2.date_input("Date", value=dt.date.today(), key=f"mock_date_{round_}")
    with st.expander("Marking scheme"):
        m1, m2 = st.columns(2)
        correct_mark = m1.number_input("Correct", value=float(config["marking"]["correct"]),
                                       step=0.25)
        wrong_mark = m2.number_input("Wrong", value=float(config["marking"]["wrong"]),
                                     step=0.25)
    config = {**config, "marking": {"correct": float(correct_mark),
                                    "wrong": float(wrong_mark), "skipped": 0.0}}

    clash = next((r for r in records
                  if r["mock_name"].strip().lower() == mock_name.strip().lower()), None)
    if clash:
        st.warning(f"A mock called “{clash['mock_name']}” is already saved "
                   f"({clash['mock_date']}). Analysing again will replace it.")

    analyse = st.button("Analyse and save", type="primary", disabled=not uploads)

    if not uploads:
        st.info(
            "**Getting your response sheet.** Open the SSC candidate response sheet in "
            "Chrome, then use **Print → Destination: Save as PDF** for each PART. "
            "That keeps the text and reads instantly."
        )
        backends = image_source.available_backends()
        if backends:
            st.caption(
                "A screen-capture PDF (one image per page, no text) also works. It is "
                f"read with OCR via {backends[0]}, which takes a few seconds per page "
                "and is slightly less accurate on question text. Scores are "
                "unaffected: they come from the option colours.")
        else:
            st.caption(
                "A screen-capture PDF (one image per page, no text) cannot be read "
                "here: no OCR backend is installed. Use the Print → Save as PDF route.")

    if analyse and uploads:
        name = mock_name.strip() or f"Mock {len(records) + 1}"
        with st.status(f"Reading {len(uploads)} file(s)…", expanded=True) as status:
            # Read the bytes, parse, and let them go. Nothing touches disk.
            st.write("Reading " + ", ".join(f.name for f in uploads))
            payloads = [f.getvalue() for f in uploads]
            try:
                parsed = sheet_parser.parse_many(payloads, config)
            except ParseError as exc:
                status.update(label="This PDF could not be read", state="error")
                st.error(str(exc))
                st.stop()
            finally:
                payloads = None
            if parsed.source == "image":
                st.write(f"No text layer, so the pages were read with OCR "
                         f"({parsed.ocr_backend or 'none'}).")
            st.write(f"Found {len(parsed.questions)} questions. Scoring…")

            problems = sheet_parser.validate(parsed, config, strict=False)
            topics_mod.tag_questions(parsed.questions, _topics())
            card = scoring.score(parsed.questions, config)

            rid = records_mod.record_id(name)
            store = _store(store_key)
            try:
                existing = store.get(rid)
                store.put(rid, records_mod.make_record(
                    questions=parsed.questions, card=card, meta=parsed.meta.__dict__,
                    marking=config["marking"], mock_name=name,
                    mock_date=mock_date.isoformat(), validation_passed=not problems,
                    source=parsed.source, ocr_backend=parsed.ocr_backend or "",
                    warnings=parsed.warnings, existing=existing))
            except Exception as exc:
                status.update(label="Scored, but could not save", state="error")
                st.error(f"The mock was scored but could not be saved: {exc}")
                st.stop()
            status.update(label=f"Saved “{name}”", state="complete")

        _refresh()
        st.session_state["open_id"] = rid
        st.session_state["upload_round"] = round_ + 1
        st.session_state["flash"] = (f"Replaced the saved “{name}”." if existing
                                     else f"Saved “{name}”.")
        st.rerun()


# ------------------------------------------------------------------ 3. all mocks

st.header("All mocks")

if not records:
    st.caption("Nothing saved yet.")
else:
    section_names = [s["label"] for s in records[-1]["sections"]]
    rows = []
    newest_first = list(reversed(records))
    for i, r in enumerate(newest_first):
        older = newest_first[i + 1] if i + 1 < len(newest_first) else None
        row = {
            "Date": r["mock_date"],
            "Mock": r["mock_name"],
            "Score": r["summary"]["score"],
            "Change": (f"{r['summary']['score'] - older['summary']['score']:+g}"
                       if older else ""),
            "Accuracy %": r["summary"]["accuracy"],
            "Attempted %": r["summary"]["attempt_rate"],
        }
        for name in section_names:
            s = next((s for s in r.get("sections", []) if s["label"] == name), None)
            row[SHORT.get(name, name)] = s["score"] if s else None
        row["Complete"] = "yes" if r.get("validation_passed", True) else "no"
        rows.append(row)
    table = pd.DataFrame(rows)

    st.caption("Select a row to open that mock's full report.")
    event = st.dataframe(
        table, hide_index=True, width="stretch",
        on_select="rerun", selection_mode="single-row",
        key=f"mocks_table_{st.session_state['upload_round']}",
        column_config={
            "Score": st.column_config.NumberColumn(format="%g"),
        },
    )
    picked = event.selection.rows if event and event.selection else []
    if picked:
        st.session_state["open_id"] = newest_first[picked[0]]["id"]

    history_all = records_mod.history_of(records, _topics())
    st.download_button(
        "Download all mocks (history.csv)",
        (history_all.to_csv(index=False) if history_all is not None else "").encode("utf-8"),
        file_name="history.csv", mime="text/csv",
        help="Every saved mock, one row per question. A backup, or for a spreadsheet.")


# --------------------------------------------------------------------- 4. report

open_id = st.session_state.get("open_id")
rec = next((r for r in records if r["id"] == open_id), None)
if rec is None:
    st.stop()

st.divider()
head, actions = st.columns([4, 1])
head.header(f"{rec['mock_name']} · {rec['mock_date']}")
with actions.popover("Delete this mock", width="stretch"):
    st.write(f"Delete “{rec['mock_name']}” for good? Its report and its place in "
             "your progress go with it.")
    if st.button("Delete", type="primary", key=f"delete_{rec['id']}"):
        _store(store_key).delete(rec["id"])
        _refresh()
        st.session_state.pop("open_id", None)
        st.session_state["upload_round"] = round_ + 1  # clears the table selection
        st.session_state["flash_top"] = f"Deleted “{rec['mock_name']}”."
        st.rerun()

if flash:
    st.success(flash)

rep, card, questions, rconfig, problems, validation = _report_for(rec, records)

meta = rep.meta
if meta.candidate_name or meta.roll_no:
    st.caption(" · ".join(x for x in (meta.candidate_name, meta.roll_no,
                                      meta.test_date, meta.shift) if x))

if rep.trend.available:
    st.write(rep.trend.text)
    fmt = lambda xs: ", ".join(f"{t} ({x:.0f}% → {y:.0f}%)" for t, x, y in xs)  # noqa: E731
    if rep.trend.improved_topics:
        st.caption(f"Improved: {fmt(rep.trend.improved_topics)}")
    if rep.trend.declined_topics:
        st.caption(f"Slipped: {fmt(rep.trend.declined_topics)}")

if problems:
    st.error(
        f"**This sheet did not validate: {len(problems)} problem(s).** The numbers "
        "below are still shown, but treat them as provisional.")
    with st.expander("What went wrong"):
        for p in problems[:25]:
            st.write(f"- {p}")
        if len(problems) > 25:
            st.write(f"… and {len(problems) - 25} more")
        st.caption("The most common cause is uploading only some of the four PART "
                   "files. Upload all four, or a single combined sheet.")

if rec.get("source") == "image":
    st.info(
        f"**This sheet had no text layer, so it was read from the page images** "
        f"(OCR: {rec.get('ocr_backend') or 'none'}). Statuses and scores come from "
        "the option colours and are unaffected. Question text may contain OCR "
        "errors, which can also leave a topic unclassified.")
if rec.get("warnings"):
    with st.expander(f"Parser notes ({len(rec['warnings'])})"):
        for w in rec["warnings"][:40]:
            st.write(f"- {w}")

unmatched = topics_mod.unmatched_rows(questions)
tab_summary, tab_topics, tab_plan, tab_wrong = st.tabs(
    ["Summary", "Topics", "7-day plan", "Wrong answers"])

with tab_summary:
    o = card.overall
    tiles = [
        ("Score", f"{o.score:g}", f"out of {o.max_score:g}"),
        ("Accuracy", _pct(o.accuracy), f"{o.correct} of {o.attempted} attempted"),
        ("Attempt rate", _pct(o.attempt_rate), f"{o.skipped} left blank"),
        ("Wrong", f"{o.wrong}", f"{o.wrong * rconfig['marking']['wrong']:g} marks"),
    ]
    for col, (label, value, note) in zip(st.columns(4), tiles):
        col.metric(label, value)
        col.caption(note)

    st.subheader("By section")
    st.dataframe(scoring.breakdown_frame(card.sections), hide_index=True,
                 width="stretch")

    st.subheader("Attempt strategy")
    st.write(rep.advice.text)

    if rep.weak:
        st.subheader("Weak areas")
        st.caption("Sorted by marks lost. The top of this list is where a week of "
                   "work pays best.")
        st.dataframe(pd.DataFrame([{
            "Topic": w.topic, "Section": w.section, "Questions": w.questions,
            "Correct": w.correct, "Skipped": w.skipped,
            "Accuracy": round(w.accuracy * 100, 1), "Marks lost": w.marks_lost,
            "Why": w.reason} for w in rep.weak]), hide_index=True,
            width="stretch")
    else:
        st.success("No topic fell below the thresholds in config.yaml.")

with tab_topics:
    st.subheader("Every topic, worst first")
    st.dataframe(scoring.breakdown_frame(card.topics), hide_index=True,
                 width="stretch")
    if unmatched:
        st.warning(
            f"{len(unmatched)} question(s) did not match any keyword in topics.yaml "
            "and are grouped as Unclassified. Download the list and add the missing "
            "keywords.")
        st.download_button(
            "Download unmatched.csv",
            topics_mod.unmatched_csv(questions).encode("utf-8"),
            file_name="unmatched.csv", mime="text/csv")

with tab_plan:
    weakest = report_mod.weakest_sections(card, 2)
    st.caption(f"Built around your two weakest sections: {weakest[0].label} and "
               f"{weakest[1].label if len(weakest) > 1 else weakest[0].label}.")
    for day in rep.plan:
        with st.expander(f"Day {day.day}: {day.focus}", expanded=day.day == 1):
            for title, body in day.blocks:
                st.markdown(f"**{title}**")
                st.write(body)

with tab_wrong:
    st.subheader(f"Wrong answers ({len(rep.wrong)})")
    if rep.wrong.empty:
        st.success("Nothing wrong on this paper.")
    else:
        st.dataframe(rep.wrong, hide_index=True, width="stretch")
    st.subheader(f"Skipped ({len(rep.skipped)})")
    if rep.skipped.empty:
        st.info("Nothing was left blank.")
    else:
        st.dataframe(rep.skipped, hide_index=True, width="stretch")

left, right = st.columns(2)
left.download_button(
    "Download report (Markdown)",
    report_mod.to_markdown(rep, _templates()).encode("utf-8"),
    file_name=f"{rec['mock_name'].replace(' ', '_').lower()}_analysis.md",
    mime="text/markdown", type="primary")
with right.popover("Validation details"):
    st.code(validation, language="text")
