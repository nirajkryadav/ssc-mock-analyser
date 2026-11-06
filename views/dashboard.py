"""Dashboard: where you stand, how you are trending, and what to work on."""
import statistics

import streamlit as st

import ui

records = ui.load_records()
ui.show_flash()

if not records:
    ui.page_header("Dashboard", "Your progress across every SSC mock you analyse.")
    with st.container(border=True):
        st.markdown('<p class="card-title">No mocks yet</p>', unsafe_allow_html=True)
        st.write("Upload the response sheet of a mock you've taken. It's read, scored "
                 "and saved, and this dashboard fills in: your score trend, accuracy "
                 "by section, and the topics that cost you the most marks.")
        st.page_link(ui.PAGE_ADD, label="Add your first mock", icon=":material/upload_file:")
    st.stop()

last, prev = records[-1], (records[-2] if len(records) > 1 else None)
best = max(records, key=lambda r: r["summary"]["score"])
scores = [r["summary"]["score"] for r in records]
recent = scores[-5:]

ui.page_header(
    "Dashboard",
    f"{len(records)} mock{'s' if len(records) != 1 else ''} · "
    f"{records[0]['mock_date']} to {last['mock_date']}",
    action=("Add a mock", ui.PAGE_ADD, ":material/add:"))

# ------------------------------------------------------------------ headline tiles
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Latest score", ui.num(last["summary"]["score"]),
          f"{last['summary']['score'] - prev['summary']['score']:+g}" if prev else None,
          delta_description=f"vs {prev['mock_name']}" if prev else None,
          help=f"{last['mock_name']}, out of {ui.num(last['summary']['max_score'])}",
          border=True, height="stretch")
k2.metric("Best score", ui.num(best["summary"]["score"]), border=True, height="stretch",
          help=f"{best['mock_name']} · {best['mock_date']}")
k3.metric(f"Average, last {len(recent)}", f"{statistics.mean(recent):.1f}".removesuffix(".0"), border=True, height="stretch",
          help="Mean score over your most recent mocks (up to five).")
k4.metric("Latest accuracy", f"{last['summary']['accuracy']:.1f}%",
          f"{last['summary']['accuracy'] - prev['summary']['accuracy']:+.1f} pts"
          if prev else None, border=True, height="stretch",
          help="Of the questions attempted, the share answered correctly.")
k5.metric("Latest attempted", f"{last['summary']['attempt_rate']:.0f}%",
          f"{last['summary']['attempt_rate'] - prev['summary']['attempt_rate']:+.0f} pts"
          if prev else None, border=True, delta_color="off", height="stretch",
          help="Share of all questions you answered.")

# ------------------------------------------------------- score trend + latest mock
left, right = st.columns([2, 1])
with left.container(border=True, height="stretch"):
    st.markdown('<p class="card-title">Score by mock</p>'
                f'<p class="card-sub">Out of {ui.num(last["summary"]["max_score"])}. '
                'Hover a point for details.</p>', unsafe_allow_html=True)
    st.altair_chart(ui.score_chart(records, height=330), width="stretch")

with right.container(border=True, height="stretch"):
    st.markdown('<p class="card-title">Latest mock</p>'
                f'<p class="card-sub">{last["mock_name"]} · {last["mock_date"]}</p>'
                f'<div class="big-score">{ui.num(last["summary"]["score"])} '
                f'<small>/ {ui.num(last["summary"]["max_score"])}</small></div>',
                unsafe_allow_html=True)
    s = last["summary"]
    st.caption(f"{s['correct']} correct · {s['wrong']} wrong · {s['skipped']} skipped")
    for sec in last.get("sections", []):
        share = sec["score"] / sec["max_score"] if sec["max_score"] else 0
        st.progress(max(0.0, min(1.0, share)),
                    text=f"{ui.short(sec['label'])} — {ui.num(sec['score'])} / "
                         f"{ui.num(sec['max_score'])}")
    if st.button("Open full report", icon=":material/arrow_forward:", type="primary",
                 width="stretch", key="open_latest"):
        ui.open_mock(last["id"])

# ------------------------------------------------------------ accuracy by section
with st.container(border=True):
    st.markdown('<p class="card-title">Accuracy by section</p>'
                '<p class="card-sub">Of the questions you attempted, the share you got '
                'right, mock by mock. The number is the latest.</p>',
                unsafe_allow_html=True)
    sections = [s["label"] for s in last.get("sections", [])]
    for col, name in zip(st.columns(len(sections) or 1), sections):
        col.altair_chart(ui.section_trend(records, name), width="stretch")

# -------------------------------------------------------- weak topics + recent mocks
left, right = st.columns([3, 2])
with left.container(border=True, height="stretch"):
    span = f"all {len(records)} mocks" if len(records) > 1 else "this mock"
    st.markdown('<p class="card-title">Topics costing you the most marks</p>'
                f'<p class="card-sub">Marks lost per topic, added up over {span}. '
                'Revise from the top.</p>', unsafe_allow_html=True)
    losses = ui.topic_losses(records)
    if losses.empty:
        st.success("No marks lost to any tagged topic.")
    else:
        st.altair_chart(ui.bars_chart(losses, "Topic", "Marks lost"), width="stretch")

with right.container(border=True, height="stretch"):
    st.markdown('<p class="card-title">Recent mocks</p>', unsafe_allow_html=True)
    newest = list(reversed(records))
    for i, r in enumerate(newest[:6]):
        older = newest[i + 1] if i + 1 < len(newest) else None
        change = r["summary"]["score"] - older["summary"]["score"] if older else None
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.markdown(f"**{r['mock_name']}**  \n:gray[{r['mock_date']}]", width="stretch")
            st.markdown(
                f"**{ui.num(r['summary']['score'])}**"
                + (f"  \n:{'green' if change >= 0 else 'red'}[{change:+g}]"
                   if change is not None else "  \n:gray[first]"),
                width="content")
            if st.button("Open", key=f"open_{r['id']}", type="tertiary",
                         icon=":material/chevron_right:", icon_position="right"):
                ui.open_mock(r["id"])
    st.page_link(ui.PAGE_MOCKS, label=f"See all {len(records)} mocks",
                 icon=":material/table_rows:")

st.caption(f"{ui.storage_label()} · {len(records)} mock(s)")
