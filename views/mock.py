"""One mock's full report: score, sections, every response, topics, plan and the PDF."""
import streamlit as st

import report as report_mod
import scoring
import topics as topics_mod
import ui

records = ui.load_records()
rid = st.query_params.get("id", "")
idx = next((i for i, r in enumerate(records) if r["id"] == rid), None)

if idx is None:
    ui.page_header("Mock not found")
    st.warning("That mock isn't saved (it may have been deleted).", icon=":material/info:")
    st.page_link(ui.PAGE_MOCKS, label="Back to all mocks", icon=":material/arrow_back:")
    st.stop()

rec = records[idx]
older = records[idx - 1] if idx > 0 else None
newer = records[idx + 1] if idx + 1 < len(records) else None
rep, card, questions, cfg, problems, validation = ui.report_for(rec, records)
o, marking = card.overall, cfg["marking"]

# ------------------------------------------------------------------------ header
with st.container(horizontal=True, vertical_alignment="center"):
    st.page_link(ui.PAGE_MOCKS, label="All mocks", icon=":material/arrow_back:")
    st.space("stretch")
    if st.button("Older", icon=":material/chevron_left:", disabled=older is None,
                 help=older and f"{older['mock_name']} · {older['mock_date']}"):
        ui.open_mock(older["id"])
    if st.button("Newer", icon=":material/chevron_right:", icon_position="right",
                 disabled=newer is None,
                 help=newer and f"{newer['mock_name']} · {newer['mock_date']}"):
        ui.open_mock(newer["id"])

meta = rep.meta
who = " · ".join(x for x in (meta.candidate_name, meta.roll_no, meta.test_date, meta.shift) if x)
ui.page_header(rec["mock_name"], " · ".join(x for x in (rec["mock_date"], who) if x))
ui.show_flash()

rank = sorted((r["summary"]["score"] for r in records), reverse=True).index(rec["summary"]["score"]) + 1 \
    if len(records) > 1 else None
k = st.columns(5)
k[0].metric("Score", f"{ui.num(o.score)} / {ui.num(o.max_score)}",
            f"{o.score - older['summary']['score']:+g}" if older else None,
            delta_description=f"vs {older['mock_name']}" if older else None, border=True)
k[1].metric("Accuracy", ui.pct(o.accuracy), border=True,
            help=f"{o.correct} right of {o.attempted} attempted")
k[2].metric("Attempted", f"{o.attempted} / {o.total}", border=True,
            help=f"{ui.pct(o.attempt_rate)} of the paper")
k[3].metric("Wrong", o.wrong, border=True, delta_color="off",
            delta=f"{o.wrong * marking['wrong']:g} marks" if o.wrong else None,
            delta_arrow="off")
k[4].metric("Rank", f"{rank} of {len(records)}" if rank else "—",
            border=True, help="Among all your saved mocks, by score. 1 = your best.")

if rep.trend.available:
    with st.container(border=True):
        st.markdown(f'<p class="card-title">Compared with your earlier mocks</p>',
                    unsafe_allow_html=True)
        st.write(rep.trend.text)
        fmt = lambda xs: ", ".join(f"{t} ({x:.0f}% → {y:.0f}%)" for t, x, y in xs)  # noqa: E731
        if rep.trend.improved_topics:
            st.markdown(f":green[**Improved:**] {fmt(rep.trend.improved_topics)}")
        if rep.trend.declined_topics:
            st.markdown(f":red[**Slipped:**] {fmt(rep.trend.declined_topics)}")

if problems:
    st.error(f"**This sheet did not validate: {len(problems)} problem(s).** The numbers "
             "are still shown, but treat them as provisional. The usual cause is "
             "uploading only some of the four PART files.", icon=":material/error:")
if rec.get("source") == "image":
    st.info("**Read from page images (OCR).** Scores come from the option colours and are "
            "unaffected; question text may contain small OCR errors.",
            icon=":material/document_scanner:")

# -------------------------------------------------------------------------- tabs
files_meta = rec.get("files") or []
t_over, t_resp, t_topics, t_plan, t_pdf = st.tabs([
    "Overview", f"Responses ({o.total})", "Topics", "7-day plan",
    f"Original PDF{' (' + str(len(files_meta)) + ')' if files_meta else ''}"])

with t_over:
    left, right = st.columns([3, 2])
    with left.container(border=True, height="stretch"):
        st.markdown('<p class="card-title">How each section went</p>'
                    '<p class="card-sub">Questions per section by outcome.</p>',
                    unsafe_allow_html=True)
        st.altair_chart(ui.outcome_chart(card), width="stretch")
    with right.container(border=True, height="stretch"):
        st.markdown('<p class="card-title">Section scores</p>', unsafe_allow_html=True)
        for b in card.sections:
            share = b.score / b.max_score if b.max_score else 0
            st.progress(max(0.0, min(1.0, share)),
                        text=f"**{ui.short(b.label)}** — {ui.num(b.score)} / "
                             f"{ui.num(b.max_score)} · accuracy {ui.pct(b.accuracy)}")
    with st.container(border=True):
        st.markdown('<p class="card-title">Attempt strategy</p>', unsafe_allow_html=True)
        st.write(rep.advice.text)
    with st.container(border=True):
        st.markdown('<p class="card-title">Weak areas</p>'
                    '<p class="card-sub">Sorted by marks lost: the top of this list is '
                    'where a week of work pays best.</p>', unsafe_allow_html=True)
        if rep.weak:
            st.dataframe([{
                "Topic": w.topic, "Section": ui.short(w.section), "Questions": w.questions,
                "Correct": w.correct, "Skipped": w.skipped,
                "Accuracy %": round(w.accuracy * 100, 1), "Marks lost": w.marks_lost,
                "Why": w.reason} for w in rep.weak], hide_index=True, width="stretch")
        else:
            st.success("No topic fell below the thresholds in config.yaml.")
    with st.expander("Validation details"):
        st.code(validation, language="text")
        for w in (rec.get("warnings") or [])[:40]:
            st.caption(f"• {w}")

with t_resp:
    counts = {s: sum(q.status == s for q in questions) for s in ("wrong", "skipped", "correct")}
    labels = {"all": f"All {len(questions)}", "wrong": f"Wrong {counts['wrong']}",
              "skipped": f"Skipped {counts['skipped']}",
              "correct": f"Correct {counts['correct']}"}
    f1, f2, f3 = st.columns([3, 2, 2])
    status = f1.segmented_control("Show", list(labels), format_func=labels.get,
                                  default="all", key=f"st_{rid}") or "all"
    secs = ["All sections"] + [b.label for b in card.sections]
    sec = f2.selectbox("Section", secs, format_func=ui.short, key=f"sec_{rid}")
    search = f3.text_input("Search", placeholder="Word in the question or topic",
                           key=f"q_{rid}")
    sel = [q for q in questions
           if (status == "all" or q.status == status)
           and (sec == "All sections" or q.section_name == sec)
           and search.lower() in f"{q.question_text} {q.topic or ''}".lower()]
    per_page = 20
    pages = max(1, -(-len(sel) // per_page))
    page = 1
    if pages > 1:
        page = st.segmented_control(
            f"Page (of {len(sel)} questions)", list(range(1, pages + 1)), default=1,
            key=f"pg_{rid}_{status}_{sec}_{search}") or 1
    if not sel:
        st.caption("No question matches these filters.")
    else:
        st.html(ui.question_cards(sel[(page - 1) * per_page: page * per_page], marking))

with t_topics:
    st.dataframe(scoring.breakdown_frame(card.topics), hide_index=True,
                 width="stretch")
    unmatched = topics_mod.unmatched_rows(questions)
    if unmatched:
        st.caption(f"{len(unmatched)} question(s) did not match any keyword in topics.yaml "
                   "and are grouped as Unclassified.")
        st.download_button("Download unmatched.csv",
                           topics_mod.unmatched_csv(questions).encode("utf-8"),
                           file_name="unmatched.csv", mime="text/csv")

with t_plan:
    weakest = report_mod.weakest_sections(card, 2)
    st.caption(f"Built around your two weakest sections: "
               f"{' and '.join(dict.fromkeys(ui.short(w.label) for w in weakest))}.")
    for day in rep.plan:
        with st.expander(f"Day {day.day}: {day.focus}", expanded=day.day == 1):
            for title, body in day.blocks:
                st.markdown(f"**{title}**")
                st.write(body)

with t_pdf:
    if not files_meta:
        st.info("The original PDF wasn't kept for this mock (it was analysed before PDFs "
                "were saved). Every response is still in the **Responses** tab.",
                icon=":material/info:")
    else:
        files = ui.load_files(rec)
        if not files:
            st.warning("The PDF for this mock couldn't be found in storage.")
        else:
            names = [n for n, _ in files]
            pick = 0
            if len(files) > 1:
                pick = st.segmented_control("File", list(range(len(files))),
                                            format_func=lambda i: names[i], default=0,
                                            key=f"pdf_{rid}") or 0
            name, data = files[pick]
            st.download_button(f"Download {name}", data, file_name=name,
                               mime="application/pdf", icon=":material/download:")
            try:
                st.pdf(data, height=900)
            except Exception:  # streamlit-pdf not installed
                st.caption("Download the file to view it.")

# ---------------------------------------------------------------- footer actions
st.divider()
with st.container(horizontal=True):
    st.download_button(
        "Download report (Markdown)",
        report_mod.to_markdown(rep, ui.templates()).encode("utf-8"),
        file_name=f"{rec['mock_name'].replace(' ', '_').lower()}_analysis.md",
        mime="text/markdown", icon=":material/download:")
    st.space("stretch")
    with st.popover("Delete this mock", icon=":material/delete:"):
        st.write(f"Delete “{rec['mock_name']}” for good? Its report, saved PDF and its "
                 "place in your progress go with it.")
        if st.button("Delete", type="primary", key=f"delete_{rec['id']}"):
            ui.store().delete(rec["id"])
            ui.refresh()
            ui.flash(f"Deleted “{rec['mock_name']}”.")
            st.switch_page(ui.PAGE_MOCKS)
if rec.get("analysis_seconds") is not None:
    st.caption(f"Analysed in {rec['analysis_seconds']:g} s · {ui.storage_label()}")
