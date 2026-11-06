"""All mocks: one table, searchable and sortable; open, download or delete any of them."""
import pandas as pd
import streamlit as st

import records as records_mod
import ui

records = ui.load_records()
ui.show_flash()
ui.page_header("All mocks",
               f"{len(records)} saved · select a row to open, download or delete it"
               if records else "Every mock you analyse is listed here.",
               action=("Add a mock", ui.PAGE_ADD, ":material/add:"))

if not records:
    st.info("Nothing saved yet.", icon=":material/info:")
    st.page_link(ui.PAGE_ADD, label="Add your first mock", icon=":material/upload_file:")
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

section_names = [s["label"] for s in records[-1]["sections"]]
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
        s = next((s for s in r.get("sections", []) if s["label"] == name), None)
        row[ui.short(name).replace("General Awareness", "GA")] = s["score"] if s else None
    row["PDF"] = "✓" if r.get("files") else "—"
    rows.append(row)

if not rows:
    st.caption("No mock matches that search.")
    st.stop()

event = st.dataframe(
    pd.DataFrame(rows), hide_index=True, width="stretch",
    on_select="rerun", selection_mode="single-row", key=f"table_{query}_{sort}",
    height="auto" if len(rows) <= 14 else 560,
    column_config={
        "Date": st.column_config.TextColumn(width=96),
        "Mock": st.column_config.TextColumn(width=130),
        "Score": st.column_config.ProgressColumn(
            min_value=0, max_value=float(max_score), format="%g", width=150,
            help=f"Out of {ui.num(max_score)}"),
        "Change": st.column_config.TextColumn(help="Score against the mock taken before it",
                                              width=70),
        "Accuracy": st.column_config.NumberColumn(format="%.1f%%", width=80),
        "Attempted": st.column_config.NumberColumn(format="%.0f%%", width=86),
        "PDF": st.column_config.TextColumn(help="Original PDF saved with the mock",
                                           width="small"),
    },
)
picked = event.selection.rows if event and event.selection else []

if picked:
    rec = shown[picked[0]]
    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        st.markdown(f"**{rec['mock_name']}** · {rec['mock_date']} · "
                    f"{ui.num(rec['summary']['score'])} / "
                    f"{ui.num(rec['summary']['max_score'])}", width="stretch")
        if st.button("Open report", type="primary", icon=":material/description:"):
            ui.open_mock(rec["id"])
        with st.popover("Delete", icon=":material/delete:"):
            st.write(f"Delete “{rec['mock_name']}” for good? Its report, saved PDF and "
                     "its place in your progress go with it.")
            if st.button("Delete", type="primary", key=f"delete_{rec['id']}"):
                ui.store().delete(rec["id"])
                ui.refresh()
                ui.flash(f"Deleted “{rec['mock_name']}”.")
                st.rerun()
else:
    st.caption("Select a row to open, download or delete that mock.")

history_all = records_mod.history_of(records, ui.topics())
st.download_button(
    "Download every mock as CSV",
    (history_all.to_csv(index=False) if history_all is not None else "").encode("utf-8"),
    file_name="history.csv", mime="text/csv", icon=":material/download:",
    help="One row per question across all saved mocks. A backup, or for a spreadsheet.")
