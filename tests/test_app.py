"""Smoke tests for the Streamlit app using Streamlit's own AppTest.

AppTest cannot drive a file upload, so the saved-mock paths are tested by
seeding the local record file directly, the same shape the app writes.
"""
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

import parser as sheet_parser  # noqa: E402
import records  # noqa: E402
import scoring  # noqa: E402
import topics as topics_mod  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def record_file(tmp_path, monkeypatch):
    path = tmp_path / "records.json"
    monkeypatch.setenv("RECORDS_FILE", str(path))
    monkeypatch.delenv("FIREBASE_KEY_FILE", raising=False)
    return path


@pytest.fixture(scope="module")
def parsed(sample_pdf):
    config = sheet_parser.load_config()
    result = sheet_parser.parse_pdf(sample_pdf.read_bytes(), config)
    topics_mod.tag_questions(result.questions, topics_mod.load_topics())
    return result


def _seed(path, parsed, mocks):
    config = sheet_parser.load_config()
    store = records.FileStore(path)
    for name, date in mocks:
        card = scoring.score(parsed.questions, config)
        rec = records.make_record(
            questions=parsed.questions, card=card, meta=parsed.meta.__dict__,
            marking=config["marking"], mock_name=name, mock_date=date,
            validation_passed=True)
        store.put(rec["id"], rec)
    return store


def _run(**state):
    at = AppTest.from_file(APP, default_timeout=60)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def test_app_renders_with_nothing_saved(record_file):
    at = _run()
    assert not at.exception
    assert at.title[0].value == "SSC Mock Analyser"
    assert any("No mocks saved yet" in m.value for m in at.info)
    # With no file chosen the app explains how to get a usable PDF.
    assert any("Save as PDF" in m.value for m in at.info)
    assert at.text_input[0].value == "Mock 1"


def test_marking_scheme_is_editable(record_file):
    at = _run()
    labels = [n.label for n in at.number_input]
    assert "Correct" in labels and "Wrong" in labels


def test_progress_shows_saved_mocks(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01"), ("Mock 2", "2026-10-15")])
    at = _run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Mocks analysed"] == "2"
    assert metrics["Latest score"] == "85"
    assert len(at.dataframe) == 1  # the all-mocks table
    assert at.text_input[0].value == "Mock 3"  # next name is suggested


def test_name_clash_warns_before_replacing(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01")])
    at = _run()
    at.text_input[0].set_value("mock 1").run()
    assert any("already saved" in w.value for w in at.warning)


def test_opening_a_saved_mock_shows_its_report(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01"), ("Mock 2", "2026-10-15")])
    at = _run(open_id="mock-2")
    assert not at.exception
    assert any(h.value.startswith("Mock 2") for h in at.header)
    assert any("Against Mock 1" in m.value for m in at.markdown)
    assert [t.label for t in at.tabs] == ["Summary", "Topics", "7-day plan", "Wrong answers"]


def test_deleting_a_mock_removes_it(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01"), ("Mock 2", "2026-10-15")])
    at = _run(open_id="mock-2")
    at.button(key="delete_mock-2").click().run()
    assert not at.exception
    assert records.FileStore(record_file).get("mock-2") is None
    assert {m.label: m.value for m in at.metric}["Mocks analysed"] == "1"
    assert any("Deleted" in s.value for s in at.success)
