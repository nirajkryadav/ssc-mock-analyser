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
import streamlit as st

import ui

st.set_page_config(page_title="SSC Mock Analyser", page_icon="📝", layout="wide")

page = st.navigation([
    st.Page(ui.PAGE_DASHBOARD, title="Dashboard", icon=":material/space_dashboard:",
            default=True),
    st.Page(ui.PAGE_MOCKS, title="All mocks", icon=":material/table_rows:",
            url_path="mocks"),
    st.Page(ui.PAGE_MOCK, title="Mock report", icon=":material/description:",
            url_path="mock"),
    st.Page(ui.PAGE_ADD, title="Add a mock", icon=":material/upload_file:", url_path="add"),
], position="hidden")  # our own top bar, so it shows in embedded mode too

ui.apply_css()
ui.topbar()
page.run()
