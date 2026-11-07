"""Syllabus: every SSC CGL Tier-1 topic, section by section, ticked off as you finish."""
import streamlit as st

import syllabus as sy
import ui

DONE = "syllabus_done"  # session copy of the ticked subtopic ids


def _load() -> None:
    if DONE in st.session_state:
        return
    try:
        st.session_state[DONE] = sy.done_set(ui.store().get_doc(sy.DOC_NAME))
    except Exception as exc:
        st.session_state[DONE] = set()
        st.error(f"**Could not load your syllabus progress.**\n\n{exc}")


def _save() -> None:
    try:
        ui.store().put_doc(sy.DOC_NAME, {"done": sorted(st.session_state[DONE])})
    except Exception as exc:
        st.toast(f"Not saved: {exc}", icon=":material/error:")


def _key(item: str) -> str:
    return "sy::" + item


def _toggle(item: str) -> None:
    done = st.session_state[DONE]
    (done.add if st.session_state[_key(item)] else done.discard)(item)
    _save()


def _set_all(topic: sy.Topic, value: bool) -> None:
    done = st.session_state[DONE]
    for item in topic.ids():
        (done.add if value else done.discard)(item)
        st.session_state[_key(item)] = value
    _save()


ui.page_header("Syllabus", "Every SSC CGL Tier-1 topic, section by section. Tick each "
               "subtopic as you finish it; your ticks are saved and follow you to any device.")
_load()
done: set[str] = st.session_state[DONE]
syl = ui.syllabus()
cfg = ui.config()
names = {s["key"]: ui.short(s["name"]) for s in cfg["sections"]}
analysis = cfg.get("analysis", {})
stats = sy.mock_stats(ui.load_records(), {s["name"]: s["key"] for s in cfg["sections"]})


def weak(topic: sy.Topic) -> bool:
    return sy.is_weak(sy.topic_mock_stats(topic, stats),
                      analysis.get("weak_topic_accuracy", 0.6),
                      analysis.get("weak_topic_skip_rate", 0.5),
                      analysis.get("weak_topic_min_questions", 2))


# ---------------------------------------------------------------- progress tiles

all_topics = [t for topics in syl.values() for t in topics]
got, total = sy.count(all_topics, done)
with st.container(horizontal=True, gap="small"):
    for label, (d, n) in [("Overall", (got, total))] + [
            (names.get(key, key), sy.count(topics, done)) for key, topics in syl.items()]:
        with st.container(border=True, width="stretch"):
            pct = d / n if n else 0
            st.metric(label, "<1%" if 0 < pct < 0.01 else f"{pct:.0%}", f"{d} of {n} subtopics", delta_color="off",
                      delta_arrow="off")
            st.progress(pct)

# ------------------------------------------------------------------- filters

with st.container(horizontal=True, vertical_alignment="bottom"):
    show = st.segmented_control(
        "Show", ["All topics", "Not finished", "Weak in mocks"], default="All topics",
        required=True, key="sy_show")
    query = st.text_input("Find a topic", placeholder="e.g. trigonometry, idioms",
                          key="sy_query", width=320).strip().lower()

if not stats:
    st.caption("Once you've saved a mock, each topic also shows how you did on it in "
               "your mocks, and *Weak in mocks* lists the ones costing you marks.")


def visible(topic: sy.Topic) -> bool:
    if query and query not in topic.name.lower() and not any(
            query in s.lower() for s in topic.subtopics):
        return False
    if show == "Not finished":
        return sy.count([topic], done)[0] < len(topic.subtopics)
    if show == "Weak in mocks":
        return weak(topic)
    return True


def mock_line(topic: sy.Topic) -> str | None:
    s = sy.topic_mock_stats(topic, stats)
    if not s or not s["questions"]:
        return None
    acc = f"{s['accuracy']:.0%} accuracy" if s["attempted"] else "none attempted"
    return (f"In your mocks: {int(s['questions'])} questions · {acc} · "
            f"{int(s['wrong'])} wrong · {int(s['skipped'])} skipped · "
            f"{ui.num(s['marks_lost'])} marks lost")


# -------------------------------------------------------------------- checklist

tabs = st.tabs([names.get(key, key) for key in syl], key="sy_tabs")
for tab, (section, topics) in zip(tabs, syl.items()):
    with tab:
        shown = [(i, t) for i, t in enumerate(topics) if visible(t)]
        if not shown:
            st.info("Nothing here matches.", icon=":material/filter_alt:")
        for i, topic in shown:
            d, n = sy.count([topic], done)
            is_weak = weak(topic)
            icon = (":material/check_circle:" if d == n else
                    ":material/priority_high:" if is_weak else
                    ":material/radio_button_unchecked:")
            label = f"**{topic.name}** — {d}/{n}" + ("  ·  weak in mocks" if is_weak else "")
            with st.expander(label, icon=icon, key=f"sy_exp_{section}_{i}",
                             expanded=bool(query)):
                line = mock_line(topic)
                if line:
                    st.caption(line)
                cols = st.columns(2)
                for j, (item, sub) in enumerate(zip(topic.ids(), topic.subtopics)):
                    k = _key(item)
                    if k not in st.session_state:
                        st.session_state[k] = item in done
                    cols[j % 2].checkbox(sub, key=k, on_change=_toggle, args=(item,))
                with st.container(horizontal=True, gap="small"):
                    st.button("Mark all done", key=f"sy_all_{section}_{i}",
                              on_click=_set_all, args=(topic, True), type="tertiary",
                              icon=":material/done_all:", disabled=d == n)
                    st.button("Clear", key=f"sy_clear_{section}_{i}",
                              on_click=_set_all, args=(topic, False), type="tertiary",
                              icon=":material/restart_alt:", disabled=d == 0)

st.caption(f"{ui.storage_label()}. The topic list is in syllabus.yaml; edit it to add "
           "your own.")
