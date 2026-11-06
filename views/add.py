"""Add a mock: upload the response sheet, and it is read, scored and saved."""
import datetime as dt
import time

import streamlit as st

import image_source
import parser as sheet_parser
import records as records_mod
import scoring
import topics as topics_mod
import ui
from parser import ParseError

records = ui.load_records()
config = ui.config()
st.session_state.setdefault("upload_round", 0)
round_ = st.session_state["upload_round"]

ui.page_header("Add a mock",
               "Upload the response sheet. It's read, scored and saved, then the full "
               "report opens. The PDF itself isn't kept: every question, your answer and "
               "the correct one are saved in the report.")

left, right = st.columns([3, 2], gap="large")

with left.container(border=True):
    uploads = st.file_uploader(
        "Response sheet PDFs", type=["pdf"], accept_multiple_files=True,
        key=f"uploads_{round_}",
        help="SSC serves one PDF per PART, so upload all four, or one combined sheet.")
    c1, c2 = st.columns(2)
    mock_name = c1.text_input("Mock name", value=f"Mock {len(records) + 1}",
                              key=f"mock_name_{round_}_{len(records)}")
    mock_date = c2.date_input("Date taken", value=dt.date.today(), key=f"mock_date_{round_}",
                              format="DD/MM/YYYY")
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
                   f"({clash['mock_date']}). Analysing again will replace it.",
                   icon=":material/warning:")

    analyse = st.button("Analyse and save", type="primary", disabled=not uploads,
                        icon=":material/play_arrow:", width="stretch")

with right.container(border=True):
    st.markdown('<p class="card-title">Getting your response sheet</p>',
                unsafe_allow_html=True)
    st.markdown(
        "1. Open the SSC candidate response sheet in **Chrome**.\n"
        "2. Press **Print → Destination: Save as PDF** for each PART (A–D).\n"
        "3. Upload all four files here together.")
    st.caption("PDFs saved this way keep their text and are read in a few seconds.")
    backends = image_source.available_backends()
    if backends:
        st.caption(f"A screen-capture PDF (pictures of the pages, no text) also works. "
                   f"It's read with OCR ({backends[0]}), which takes longer and may "
                   "misread some question text. Scores are unaffected: they come from "
                   "the option colours.")
    else:
        st.caption("Screen-capture PDFs (no text layer) can't be read here: no OCR "
                   "backend is installed.")

if analyse and uploads:
    name = mock_name.strip() or f"Mock {len(records) + 1}"
    started = time.perf_counter()
    with st.status(f"Reading {len(uploads)} file(s)…", expanded=True) as status:
        st.write("Reading " + ", ".join(f.name for f in uploads))
        try:
            parsed = sheet_parser.parse_many([f.getvalue() for f in uploads], config)
        except ParseError as exc:
            status.update(label="This PDF could not be read", state="error")
            st.error(str(exc))
            st.stop()
        read_s = time.perf_counter() - started
        how = (f"with OCR ({parsed.ocr_backend or 'none'})" if parsed.source == "image"
               else "from its text")
        st.write(f"Read {len(parsed.questions)} questions {how} in {read_s:.1f} s. Scoring…")

        problems = sheet_parser.validate(parsed, config, strict=False)
        topics_mod.tag_questions(parsed.questions, ui.topics())
        card = scoring.score(parsed.questions, config)
        seconds = time.perf_counter() - started

        rid = records_mod.record_id(name)
        store = ui.store()
        try:
            existing = store.get(rid)
            store.put(rid, records_mod.make_record(
                questions=parsed.questions, card=card, meta=parsed.meta.__dict__,
                marking=config["marking"], mock_name=name,
                mock_date=mock_date.isoformat(), validation_passed=not problems,
                source=parsed.source, ocr_backend=parsed.ocr_backend or "",
                warnings=parsed.warnings, existing=existing, seconds=seconds))
            if existing and existing.get("files"):
                store.put_files(rid, [])  # drop a PDF kept by an older version
        except Exception as exc:
            status.update(label="Scored, but could not save", state="error")
            st.error(f"The mock was scored but could not be saved: {exc}")
            st.stop()
        status.update(label=f"Saved “{name}” in {time.perf_counter() - started:.1f} s",
                      state="complete")

    ui.refresh()
    st.session_state["upload_round"] = round_ + 1
    ui.flash((f"Replaced the saved “{name}”." if existing else f"Saved “{name}”.")
             + f" Analysed in {seconds:.1f} s.")
    ui.open_mock(rid)
