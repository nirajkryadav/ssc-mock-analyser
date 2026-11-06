"""Shared pieces for every page of the Streamlit app: storage, caching, the top
bar, styling, charts and the question cards.

Pages live in views/ and are wired together in app.py.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

import parser as sheet_parser
import records as records_mod
import report as report_mod
import scoring
import topics as topics_mod
from parser import SheetMeta

# Light theme only (set in .streamlit/config.toml), so chart ink can be fixed.
BLUE = "#2a78d6"
GOOD = "#0ca30c"
BAD = "#d03b3b"
SKIP = "#b9b8b2"
INK = "#111827"
INK2 = "#4b5563"
GRID = "#e5e7eb"

SHORT = {
    "General Intelligence and Reasoning": "Reasoning",
    "General Awareness": "General Awareness",
    "Quantitative Aptitude": "Quant",
    "English Comprehension": "English",
}

PAGE_DASHBOARD = "views/dashboard.py"
PAGE_MOCKS = "views/mocks.py"
PAGE_MOCK = "views/mock.py"
PAGE_ADD = "views/add.py"


def target_score() -> float | None:
    """The score you're aiming for (config.yaml analysis.target_score), if set."""
    value = config().get("analysis", {}).get("target_score")
    return float(value) if value else None


def short(name: str) -> str:
    return SHORT.get(name, name)


def num(x: float) -> str:
    return f"{round(float(x), 2):g}"


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


# ------------------------------------------------------------------------ config


# The YAML settings are cached, keyed on each file's modification time:
# Streamlit Cloud applies a git push without restarting the process, so a
# plain cache would keep serving the settings from before the push.
def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


@st.cache_resource(max_entries=2)
def _config(stamp: float) -> dict[str, Any]:
    return sheet_parser.load_config()


@st.cache_resource(max_entries=2)
def _topics(stamp: float) -> dict[str, Any]:
    return topics_mod.load_topics()


@st.cache_resource(max_entries=2)
def _templates(stamp: float) -> dict[str, Any]:
    return report_mod.load_templates()


def config() -> dict[str, Any]:
    return _config(_mtime(sheet_parser.CONFIG_PATH))


def topics() -> dict[str, Any]:
    return _topics(_mtime(topics_mod.TOPICS_PATH))


def templates() -> dict[str, Any]:
    return _templates(_mtime(report_mod.TEMPLATES_PATH))


# ----------------------------------------------------------------------- storage


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


def store_key() -> str:
    key = _firebase_key()
    if key:
        return f"firestore:{key.get('project_id', '')}"
    return f"file:{os.environ.get('RECORDS_FILE', records_mod.DEFAULT_FILE)}"


@st.cache_resource(show_spinner=False)
def _store(key: str) -> records_mod.RecordStore:
    if key.startswith("firestore:"):
        return records_mod.FirestoreStore.from_service_account(_firebase_key())
    return records_mod.FileStore(Path(key.split(":", 1)[1]))


def store() -> records_mod.RecordStore:
    return _store(store_key())


@st.cache_data(ttl=300, show_spinner="Loading your mocks…")
def _load_records(key: str) -> list[dict[str, Any]]:
    return records_mod.sorted_records(_store(key).list())


def load_records() -> list[dict[str, Any]]:
    """Every saved mock, oldest first. Shows an error (not a crash) if unreachable."""
    try:
        return _load_records(store_key())
    except Exception as exc:
        st.error(f"**Could not load your saved mocks.**\n\n{exc}")
        return []


def refresh() -> None:
    _load_records.clear()


def on_streamlit_cloud() -> bool:
    return Path(__file__).resolve().parts[1:3] == ("mount", "src")


def storage_label() -> str:
    key = store_key()
    if key.startswith("firestore:"):
        return "Saved to your Firebase database"
    return "Saved on this computer (.records/)"


# ------------------------------------------------------------------- navigation


def go(label: str, page: str, icon: str | None = None, *, key: str,
       type: str = "tertiary", width: str = "content") -> None:
    """A navigation button.

    st.page_link renders a plain link, and inside Streamlit Community Cloud's
    outer page those links do nothing. A button that calls st.switch_page works
    everywhere: the cloud page, the bare app URL, and the Netlify embed.
    """
    if st.button(label, icon=icon, key=key, type=type, width=width):
        st.switch_page(page)


# Deleting a mock needs a password. Only its SHA-256 is kept here; a
# `delete_password` entry in Streamlit secrets overrides it.
DELETE_PASSWORD_SHA256 = "19cb2a070ddbe8157e17c5dda0ea38e8aa16fae1725c1f7ac22747d870368579"


def delete_password_ok(entered: str) -> bool:
    try:
        configured = st.secrets.get("delete_password")
    except Exception:  # no secrets file locally
        configured = None
    if configured:
        return hmac.compare_digest(entered.encode(), str(configured).encode())
    digest = hashlib.sha256(entered.encode()).hexdigest()
    return hmac.compare_digest(digest, DELETE_PASSWORD_SHA256)


def delete_control(rec: dict[str, Any], *, label: str = "Delete",
                   then: str | None = None, key: str = "") -> None:
    """A Delete popover that asks for the password before removing the mock."""
    rid = rec["id"]
    with st.popover(label, icon=":material/delete:"):
        st.write(f"Delete “{rec['mock_name']}” for good? Its report and its "
                 "place in your progress go with it.")
        with st.form(f"delete_form_{key}{rid}", border=False):
            pw = st.text_input("Password", type="password", key=f"pw_{key}{rid}")
            go_ = st.form_submit_button("Delete", type="primary", key=f"delete_{key}{rid}")
        if go_:
            if not delete_password_ok(pw):
                st.error("Wrong password. Nothing was deleted.")
            else:
                store().delete(rid)
                refresh()
                flash(f"Deleted “{rec['mock_name']}”.")
                if then:
                    st.switch_page(then)
                st.rerun()


def open_mock(rid: str) -> None:
    st.switch_page(PAGE_MOCK, query_params={"id": rid})


def flash(message: str) -> None:
    """Show `message` once on the next page that calls show_flash()."""
    st.session_state["flash"] = message


def show_flash() -> None:
    if msg := st.session_state.pop("flash", None):
        st.success(msg, icon=":material/check_circle:")


def topbar(current: str = "") -> None:
    with st.container(horizontal=True, vertical_alignment="center", gap="small",
                      key="topbar"):
        st.markdown('<div class="brand">📝 SSC Mock Analyser</div>',
                    unsafe_allow_html=True, width="content")
        st.space("stretch")
        for label, page, icon in (("Dashboard", PAGE_DASHBOARD, ":material/space_dashboard:"),
                                  ("All mocks", PAGE_MOCKS, ":material/table_rows:"),
                                  ("Add a mock", PAGE_ADD, ":material/upload_file:")):
            go(label, page, icon, key=f"nav_{page}",
               type="secondary" if page == current else "tertiary")
    if on_streamlit_cloud() and not store_key().startswith("firestore:"):
        st.warning("**Mocks saved here will be lost.** No Firebase key is set, so they go "
                   "to this server's disk, which Streamlit wipes whenever the app "
                   "restarts or sleeps. Add the `[firebase]` section under the app's "
                   "**Settings → Secrets** to keep them.", icon=":material/warning:")


def page_header(title: str, subtitle: str = "", action: tuple[str, str, str] | None = None):
    """Page title on the left, optional (label, page, icon) link button on the right."""
    with st.container(horizontal=True, vertical_alignment="center"):
        sub = f'<div class="page-sub">{subtitle}</div>' if subtitle else ""
        st.markdown(f'<div class="page-head"><div class="page-title">{html.escape(title)}'
                    f'</div>{sub}</div>', unsafe_allow_html=True, width="stretch")
        if action:
            label, page, icon = action
            go(label, page, icon, key=f"action_{page}", type="primary")


# ------------------------------------------------------------------------ styling


CSS = """
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 4rem; max-width: 1240px; }
/* Streamlit's header bar is invisible here but sits over the top of the page and
   swallows clicks on streamlit.app, so the top bar never received them. */
[data-testid="stHeader"] { display: none !important; }
[data-testid="stToolbarActions"], [data-testid="stAppDeployButton"],
[data-testid="stMainMenu"], [data-testid="stDecoration"] { display: none !important; }
.st-key-topbar button[kind="secondary"] { background: #eef3fb; border-color: #cfe0f6; color: #1f62b5; }
.st-key-topbar { border-bottom: 1px solid #e5e7eb; padding-bottom: .6rem; margin-bottom: .6rem; }
.brand { font-weight: 700; font-size: 1.05rem; color: #111827; white-space: nowrap; }
.page-head { margin: .2rem 0 .3rem; }
.page-title { font-size: 1.85rem; font-weight: 700; color: #111827; line-height: 1.25; letter-spacing: -.01em; }
.page-sub { color: #4b5563; margin-top: .25rem; font-size: .95rem; }
[data-testid="stMetric"] { background: #ffffff; }
[data-testid="stMetricValue"] { font-size: 1.9rem; font-weight: 700; color: #111827; }
[data-testid="stMetricLabel"] p { font-size: .85rem; color: #4b5563; font-weight: 500; }
.card-title { font-weight: 650; font-size: 1.05rem; color: #111827; margin: 0 0 .1rem; }
.card-sub { color: #6b7280; font-size: .85rem; margin: 0 0 .4rem; }
.big-score { font-size: 2.6rem; font-weight: 750; line-height: 1.1; color: #111827; }
.big-score small { font-size: 1rem; color: #6b7280; font-weight: 500; }
.muted { color: #6b7280; }

/* question cards */
.qlist { display: flex; flex-direction: column; gap: 12px; }
.q { border: 1px solid #e5e7eb; border-left: 4px solid #b9b8b2; border-radius: 10px;
     padding: 14px 16px; background: #fff; }
.q.correct { border-left-color: #0ca30c; }
.q.wrong   { border-left-color: #d03b3b; }
.q-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 6px; }
.q-no { font-weight: 700; color: #111827; }
.q-meta { color: #6b7280; font-size: .85rem; }
.pill { margin-left: auto; font-size: .78rem; font-weight: 600; padding: 2px 10px;
        border-radius: 999px; white-space: nowrap; }
.pill.correct { background: #e7f6e7; color: #0a6e0a; }
.pill.wrong   { background: #fdeaea; color: #a42828; }
.pill.skipped { background: #f1f1ef; color: #52514e; }
.q-text { color: #111827; font-size: .98rem; line-height: 1.55; margin: 2px 0 10px;
          white-space: pre-wrap; }
.opts { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; margin: 0; padding: 0; }
@media (max-width: 700px) { .opts { grid-template-columns: 1fr; } }
.opt { list-style: none; display: flex; gap: 8px; align-items: flex-start; padding: 7px 10px;
       border: 1px solid #e5e7eb; border-radius: 8px; color: #1f2937; font-size: .93rem; }
.opt .l { font-weight: 700; color: #6b7280; min-width: 1.1em; }
.opt .tag { margin-left: auto; font-size: .72rem; font-weight: 700; white-space: nowrap;
            padding-left: 6px; }
.opt.is-correct { background: #eef8ee; border-color: #9fd89f; }
.opt.is-correct .tag { color: #0a6e0a; }
.opt.is-wrong { background: #fdf0f0; border-color: #f0b4b4; }
.opt.is-wrong .tag { color: #a42828; }
</style>
"""


def apply_css() -> None:
    st.html(CSS)


# ------------------------------------------------------------------------- charts


def _style(chart: alt.Chart) -> alt.Chart:
    return (chart.properties(autosize=alt.AutoSizeParams(type="fit", contains="padding"),
                             padding={"left": 6, "top": 8, "right": 6, "bottom": 4})
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=INK2, titleColor=INK2, gridColor=GRID,
                            domainColor=GRID, tickColor=GRID, labelFontSize=12)
            .configure_title(color=INK, fontSize=13, anchor="start", fontWeight=600))


def score_chart(records: list[dict[str, Any]], height: int = 260,
                target: float | None = None) -> alt.Chart:
    names = [r["mock_name"] for r in records]
    df = pd.DataFrame([{
        "Mock": r["mock_name"], "Date": r["mock_date"],
        "Score": r["summary"]["score"], "Out of": r["summary"]["max_score"],
        "Accuracy %": r["summary"]["accuracy"], "Attempted %": r["summary"]["attempt_rate"],
    } for r in records])
    top = float(df["Out of"].max() or 200)
    x = alt.X("Mock:N", sort=names, axis=alt.Axis(labelAngle=0, title=None, labelLimit=90))
    y = alt.Y("Score:Q", scale=alt.Scale(domain=[0, top]), title=None,
              axis=alt.Axis(tickCount=5))
    base = alt.Chart(df).encode(x=x, y=y, tooltip=list(df.columns))
    chart = (
        base.mark_area(color=BLUE, opacity=0.08)
        + base.mark_line(color=BLUE, strokeWidth=2)
        + base.mark_point(color=BLUE, filled=True, size=70, opacity=1,
                          stroke="white", strokeWidth=2)
        + alt.Chart(df.tail(1)).mark_text(align="left", dx=10, fontWeight="bold",
                                          color=INK, fontSize=13)
        .encode(x=x, y=y, text=alt.Text("Score:Q", format="~g"))
    )
    if target:
        goal = pd.DataFrame({"Target": [target], "label": [f"Target {target:g}"]})
        line = alt.Chart(goal).encode(y=alt.Y("Target:Q", scale=alt.Scale(domain=[0, top])))
        chart = (line.mark_rule(color=INK2, strokeDash=[6, 4], strokeWidth=1.5)
                 .encode(tooltip=[alt.Tooltip("Target:Q", title="Your target")])
                 + line.mark_text(align="left", baseline="bottom", x=4, dy=-4,
                                  color=INK2, fontSize=12, fontWeight=600)
                 .encode(text="label:N")
                 + chart)
    return _style(chart.properties(height=height))


def section_trend(records: list[dict[str, Any]], section: str) -> alt.Chart:
    names = [r["mock_name"] for r in records]
    rows = []
    for r in records:
        s = next((s for s in r.get("sections", []) if s["label"] == section), None)
        if s:
            rows.append({"Mock": r["mock_name"], "Accuracy %": s["accuracy"],
                         "Score": s["score"], "Correct": s["correct"],
                         "Wrong": s["wrong"], "Skipped": s["skipped"]})
    df = pd.DataFrame(rows)
    x = alt.X("Mock:N", sort=names, axis=alt.Axis(labels=False, ticks=False, title=None))
    y = alt.Y("Accuracy %:Q", scale=alt.Scale(domain=[0, 100]),
              axis=alt.Axis(values=[0, 50, 100], title=None))
    base = alt.Chart(df).encode(x=x, y=y, tooltip=list(df.columns))
    chart = (
        base.mark_line(color=BLUE, strokeWidth=2)
        + base.mark_point(color=BLUE, filled=True, size=40, opacity=1)
        + alt.Chart(df.tail(1)).mark_text(align="left", dx=7, fontWeight="bold", color=INK)
        .encode(x=x, y=y, text=alt.Text("Accuracy %:Q", format=".0f"))
    ).properties(height=130, title=short(section))
    return _style(chart)


def topic_losses(records: list[dict[str, Any]], limit: int = 8) -> pd.DataFrame:
    lost: dict[str, dict[str, Any]] = {}
    for r in records:
        for t in r.get("topics", []):
            if t["label"] == "Unclassified":
                continue
            cur = lost.setdefault(t["label"], {
                "Topic": t["label"], "Section": short(t["section"]),
                "Marks lost": 0.0, "Wrong": 0, "Skipped": 0, "Mocks with losses": 0})
            cur["Marks lost"] += t["marks_lost"]
            cur["Wrong"] += t["wrong"]
            cur["Skipped"] += t["skipped"]
            cur["Mocks with losses"] += 1 if t["marks_lost"] > 0 else 0
    top = sorted((v for v in lost.values() if v["Marks lost"] > 0),
                 key=lambda v: -v["Marks lost"])[:limit]
    df = pd.DataFrame(top)
    if not df.empty:
        df["Marks lost"] = df["Marks lost"].round(2)
    return df


def bars_chart(df: pd.DataFrame, label: str, value: str) -> alt.Chart:
    y = alt.Y(f"{label}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200))
    x = alt.X(f"{value}:Q", title=None, axis=alt.Axis(tickCount=4))
    base = alt.Chart(df).encode(y=y, x=x, tooltip=list(df.columns))
    chart = (
        base.mark_bar(color=BLUE, size=14, cornerRadiusEnd=4)
        + base.mark_text(align="left", dx=5, color=INK).encode(
            text=alt.Text(f"{value}:Q", format="~g"))
    ).properties(height=30 * len(df) + 10)
    return _style(chart)


def outcome_chart(card: scoring.ScoreCard) -> alt.Chart:
    """Correct / wrong / skipped per section, as stacked bars of question counts."""
    rows = []
    order = ["Correct", "Wrong", "Skipped"]
    for b in card.sections:
        for k, n in zip(order, (b.correct, b.wrong, b.skipped)):
            rows.append({"Section": short(b.label), "Outcome": k, "Questions": n,
                         "Order": order.index(k)})
    df = pd.DataFrame(rows)
    chart = alt.Chart(df).mark_bar(size=22, stroke="white", strokeWidth=2).encode(
        y=alt.Y("Section:N", title=None, sort=[short(b.label) for b in card.sections],
                axis=alt.Axis(labelLimit=180)),
        x=alt.X("Questions:Q", title=None, stack="zero", axis=alt.Axis(tickCount=5)),
        color=alt.Color("Outcome:N", scale=alt.Scale(domain=order, range=[GOOD, BAD, SKIP]),
                        legend=alt.Legend(orient="top", title=None, labelColor=INK2)),
        order=alt.Order("Order:Q"),
        tooltip=["Section", "Outcome", "Questions"],
    ).properties(height=46 * len(card.sections) + 20)
    return _style(chart)


# ----------------------------------------------------------------------- reports


def report_for(rec: dict[str, Any], all_records: list[dict[str, Any]]):
    """Rebuild a saved mock's full report, compared against every other saved mock."""
    base = config()
    cfg = {**base, "marking": rec.get("marking") or base["marking"]}
    questions = records_mod.questions_from(rec["questions"], topics())
    stored_meta = rec.get("meta") or {}
    meta = SheetMeta(**{k: stored_meta.get(k) or "" for k in SheetMeta.__dataclass_fields__})
    card = scoring.score(questions, cfg)

    others = [r for r in all_records if r.get("id") != rec.get("id")]
    current = scoring.questions_frame(questions, rec["mock_date"], rec["mock_name"])
    history = scoring.merge_history(records_mod.history_of(others, topics()), current)

    parsed = sheet_parser.ParseResult(questions=questions, meta=meta)
    problems = sheet_parser.validate(parsed, cfg, strict=False)
    rep = report_mod.build(questions, card, cfg, meta, rec["mock_name"],
                           history=history, templates=templates())
    validation = sheet_parser.validation_report(parsed, cfg)
    return rep, card, questions, cfg, problems, validation


# ------------------------------------------------------------------ question cards


def question_cards(questions, marking: dict[str, float]) -> str:
    """HTML for a list of questions: text, options, your pick and the right answer."""
    status_text = {"correct": "Correct", "wrong": "Wrong", "skipped": "Not attempted"}
    out = ['<div class="qlist">']
    for q in questions:
        mark = {"correct": marking["correct"], "wrong": marking["wrong"]}.get(q.status, 0)
        pill = f"{status_text.get(q.status, q.status)} · {mark:+g}" if mark else \
            status_text.get(q.status, q.status)
        meta = " · ".join(html.escape(x) for x in (short(q.section_name or ""),
                                                   q.topic or "Unclassified") if x)
        opts = []
        for i, text in enumerate(q.options or []):
            cls, tag = "", ""
            if i == q.correct_index and i == q.selected_index:
                cls, tag = "is-correct", "✓ Your answer"
            elif i == q.correct_index:
                cls, tag = "is-correct", "✓ Correct answer"
            elif i == q.selected_index:
                cls, tag = "is-wrong", "✗ Your answer"
            body = (f'<span>{html.escape(text)}</span>' if text.strip() else
                    '<span class="muted">(couldn&#39;t read this option)</span>')
            opts.append(
                f'<li class="opt {cls}"><span class="l">{"ABCD"[i] if i < 4 else "?"}</span>'
                f'{body}{f"<span class=tag>{tag}</span>" if tag else ""}</li>')
        status = q.status if q.status in status_text else "skipped"
        out.append(
            f'<div class="q {status}"><div class="q-head">'
            f'<span class="q-no">Q{q.q_no}</span><span class="q-meta">{meta}</span>'
            f'<span class="pill {status}">{pill}</span></div>'
            f'<div class="q-text">{html.escape(q.question_text or "(question text not read)")}</div>'
            f'<ol class="opts">{"".join(opts)}</ol></div>')
    out.append("</div>")
    return "".join(out)
