"""SSC Mock Analyser: Streamlit entry point.

Pages (views/):
  Dashboard   progress across every saved mock
  All mocks   table of every mock; open, download or delete
  Mock        one mock's full report, every response, and its original PDF
  Add a mock  upload a response sheet; it is read, scored and saved

Where mocks go:
  * Firestore, when Streamlit secrets hold a [firebase] service-account key.
    This is how the deployed app keeps them: Streamlit Community Cloud wipes its
    disk whenever the app restarts or sleeps.
  * Otherwise .records/ on this machine.
"""
import sys
from pathlib import Path

import streamlit as st


def _drop_stale_modules() -> None:
    """Re-import this app's own modules after a deploy changed them.

    Streamlit Community Cloud applies a git push without restarting Python.
    Page scripts run fresh each time, but modules they import (parser.py,
    testbook.py, …) stay as first loaded, so a fix to the reader would not take
    effect until a reboot. Comparing file times on each run catches that.
    """
    root = Path(__file__).resolve().parent
    stamp = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in root.glob("*.py")))
    if getattr(sys, "_ssc_module_stamp", None) == stamp:
        return
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if file and name != "__main__" and Path(file).resolve().parent == root:
            del sys.modules[name]
    sys._ssc_module_stamp = stamp


_drop_stale_modules()

import ui  # noqa: E402  (after the stale-module check on purpose)

st.set_page_config(page_title="SSC Mock Analyser", page_icon="📝", layout="wide")

page = st.navigation([
    st.Page(ui.PAGE_DASHBOARD, title="Dashboard", icon=":material/space_dashboard:",
            default=True),
    st.Page(ui.PAGE_MOCKS, title="All mocks", icon=":material/table_rows:",
            url_path="mocks"),
    st.Page(ui.PAGE_MOCK, title="Mock report", icon=":material/description:",
            url_path="mock"),
    st.Page(ui.PAGE_ADD, title="Add a mock", icon=":material/upload_file:", url_path="add"),
    st.Page(ui.PAGE_SYLLABUS, title="Syllabus", icon=":material/checklist:",
            url_path="syllabus"),
], position="hidden")  # our own top bar, so it shows in embedded mode too

ui.apply_css()
NAV = {"Dashboard": ui.PAGE_DASHBOARD, "All mocks": ui.PAGE_MOCKS, "Add a mock": ui.PAGE_ADD,
       "Syllabus": ui.PAGE_SYLLABUS}
ui.topbar(current=NAV.get(page.title, ""))
page.run()
