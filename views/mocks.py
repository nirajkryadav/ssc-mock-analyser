"""All mocks: every mock with its own Open and Delete, plus a comparison table."""
import pandas as pd
import streamlit as st

import records as records_mod
import ui

records = ui.load_records()
ui.show_flash()
ui.page_header("All mocks",
               f"{len(records)} saved · open any mock to see its full analysis"
               if records else "Every mock you analyse is listed here.",
               action=("Add a mock", ui.PAGE_ADD, ":material/add:"))

if not records:
    st.info("Nothing saved yet.", icon=":material/info:")
    ui.go("Add your first mock", ui.PAGE_ADD, ":material/upload_file:", key="first",
          type="primary")
    st.stop()

SORTS = {
    "Newest first": (lambda r: (r["mock_date"], r.get("created_at", "")), True),
    "Oldest first": (lambda r: (r["mock_date"], r.get("created_at", "")), False),
    "Highest score": (lambda r: r["summary"]["score"], True),
    "Lowest score": (lambda r: r["summary"]["score"], False),
}
c1, c2 = st.columns([3, 1])
query = c1.text_input("Search", placeholder="Search by mock name or date",
                      label_visibility="collapsed", icon=":material/search:")
sort = c2.selectbox("Sort", list(SORTS), label_visibility="collapsed")

# Change is always against the mock taken just before, whatever the sort.
previous = {r["id"]: (records[i - 1] if i else None) for i, r in enumerate(records)}
key, reverse = SORTS[sort]
shown = [r for r in sorted(records, key=key, reverse=reverse)
         if query.strip().lower() in f"{r['mock_name']} {r['mock_date']}".lower()]

if not shown:
    st.caption("No mock matches that search.")
    st.stop()

section_names = [s["label"] for s in records[-1]["sections"]]
ABBR = {"Reasoning": "Reas", "General Awareness": "GA", "Quant": "Quant", "English": "Eng"}

# ------------------------------------------------------------------ one row each
for r in shown:
    p = previous[r["id"]]
    s_ = r["summary"]
    change = s_["score"] - p["summary"]["score"] if p else None
    with st.container(border=True, horizontal=True, vertical_alignment="center",
                      gap="small", key=f"row_{r['id']}"):
        st.markdown(f"**{r['mock_name']}**  \n:gray[{r['mock_date']}]", width=165)
        st.markdown(
            f"**{ui.num(s_['score'])}** :gray[/ {ui.num(s_['max_score'])}]  \n"
            + (f":{'green' if change >= 0 else 'red'}[{change:+g}] :gray[vs previous]"
               if change is not None else ":gray[first mock]"), width=150)
        st.markdown(f"**{s_['accuracy']:.0f}%** :gray[accuracy]  \n"
                    f"**{s_['attempt_rate']:.0f}%** :gray[attempted]", width=140)
        parts = []
        for name in section_names:
            sec = next((x for x in r.get("sections", []) if x["label"] == name), None)
            if sec:
                parts.append(f":gray[{ABBR.get(ui.short(name), ui.short(name))}] "
                             f"**{ui.num(sec['score'])}**")
        st.markdown(" · ".join(parts), width="content")
        st.space("stretch")
        with st.container(horizontal=True, gap="small", width="content"):
            if st.button("Open", type="primary", icon=":material/arrow_forward:",
                         key=f"open_{r['id']}"):
                ui.open_mock(r["id"])
            ui.delete_control(r, label="Delete", key="list_")

# ------------------------------------------------------------- comparison table
max_score = max(r["summary"]["max_score"] for r in records) or 200
rows = []
for r in shown:
    p = previous[r["id"]]
    row = {
        "Date": r["mock_date"],
        "Mock": r["mock_name"],
        "Score": r["summary"]["score"],
        "Change": f"{r['summary']['score'] - p['summary']['score']:+g}" if p else "first",
        "Accuracy": r["summary"]["accuracy"],
        "Attempted": r["summary"]["attempt_rate"],
    }
    for name in section_names:
        sec = next((x for x in r.get("sections", []) if x["label"] == name), None)
        row[ABBR.get(ui.short(name), ui.short(name))] = sec["score"] if sec else None
    rows.append(row)

with st.expander("Compare all mocks in a table"):
    st.dataframe(
        pd.DataFrame(rows), hide_index=True, width="stretch",
        height="content",
        column_config={
            "Score": st.column_config.ProgressColumn(
                min_value=0, max_value=float(max_score), format="%g", width=150,
                help=f"Out of {ui.num(max_score)}"),
            "Change": st.column_config.TextColumn(help="Against the mock taken before it"),
            "Accuracy": st.column_config.NumberColumn(format="%.1f%%"),
            "Attempted": st.column_config.NumberColumn(format="%.0f%%"),
        },
    )

history_all = records_mod.history_of(records, ui.topics())
st.download_button(
    "Download every mock as CSV",
    (history_all.to_csv(index=False) if history_all is not None else "").encode("utf-8"),
    file_name="history.csv", mime="text/csv", icon=":material/download:",
    help="One row per question across all saved mocks. A backup, or for a spreadsheet.")
