"""Smoke tests for the Streamlit app using Streamlit's own AppTest.

AppTest cannot drive a file upload, so the saved-mock paths are tested by
seeding the local record store directly, the same shape the app writes.
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


def _seed(path, parsed, mocks, pdf=None):
    config = sheet_parser.load_config()
    store = records.FileStore(path)
    files = [("part-a.pdf", pdf)] if pdf else None
    for name, date in mocks:
        card = scoring.score(parsed.questions, config)
        rec = records.make_record(
            questions=parsed.questions, card=card, meta=parsed.meta.__dict__,
            marking=config["marking"], mock_name=name, mock_date=date,
            validation_passed=True, files=files)
        store.put(rec["id"], rec)
        if files:
            store.put_files(rec["id"], files)
    return store


def _app(page=None, state=None, **query):
    at = AppTest.from_file(APP, default_timeout=60)
    for k, v in query.items():
        at.query_params[k] = v
    for k, v in (state or {}).items():
        at.session_state[k] = v
    if page:
        at.switch_page(page)
    return at.run()


def test_dashboard_with_nothing_saved(record_file):
    at = _app()
    assert not at.exception
    assert any("No mocks yet" in m.value for m in at.markdown)


def test_add_page_has_upload_form(record_file):
    at = _app("views/add.py")
    assert not at.exception
    assert at.text_input[0].value == "Mock 1"
    labels = [n.label for n in at.number_input]
    assert "Correct" in labels and "Wrong" in labels


def test_dashboard_shows_saved_mocks(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01"), ("Mock 2", "2026-10-15")])
    at = _app()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Latest score"] == "85"
    assert metrics["Best score"] == "85"
    assert any("2 mocks" in m.value for m in at.markdown)


def test_add_page_suggests_next_name_and_warns_on_clash(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01")])
    at = _app("views/add.py")
    assert at.text_input[0].value == "Mock 2"
    at.text_input[0].set_value("mock 1").run()
    assert any("already saved" in w.value for w in at.warning)


def test_all_mocks_lists_every_mock(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01"), ("Mock 2", "2026-10-15")])
    at = _app("views/mocks.py")
    assert not at.exception
    assert len(at.dataframe) == 1
    assert list(at.dataframe[0].value["Mock"]) == ["Mock 2", "Mock 1"]  # newest first


def test_mock_report_shows_responses(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01"), ("Mock 2", "2026-10-15")])
    at = _app("views/mock.py", id="mock-2")
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Overview", "Responses (100)", "Topics", "7-day plan"]
    assert any("Compared with your earlier mocks" in m.value for m in at.markdown)


def test_add_page_does_not_offer_to_keep_the_pdf(record_file):
    at = _app("views/add.py")
    assert not at.checkbox


def test_unknown_mock_says_so(record_file):
    at = _app("views/mock.py", id="nope")
    assert not at.exception
    assert any("isn't saved" in w.value for w in at.warning)


def test_deleting_needs_the_password(record_file, parsed, sample_pdf):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01"), ("Mock 2", "2026-10-15")],
          pdf=sample_pdf.read_bytes())
    at = _app("views/mock.py", id="mock-2")
    at.text_input(key="pw_mock-2").input("wrong")
    at.button(key="delete_mock-2").click().run()
    assert not at.exception
    assert any("Wrong password" in e.value for e in at.error)
    assert records.FileStore(record_file).get("mock-2") is not None


def test_deleting_a_mock_removes_it_and_its_pdf(record_file, parsed, sample_pdf):
    store = _seed(record_file, parsed, [("Mock 1", "2026-10-01"), ("Mock 2", "2026-10-15")],
                  pdf=sample_pdf.read_bytes())
    at = _app("views/mock.py", id="mock-2")
    at.text_input(key="pw_mock-2").input("passowrd")
    at.button(key="delete_mock-2").click().run()
    assert not at.exception
    fresh = records.FileStore(record_file)
    assert fresh.get("mock-2") is None and fresh.get_files("mock-2") == []
    assert fresh.get("mock-1") is not None and store.get_files("mock-1")


def test_all_mocks_opens_a_mock_without_selecting_a_row(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01"), ("Mock 2", "2026-10-15")])
    at = _app("views/mocks.py")
    at.button(key="open_mock-1").click().run()
    assert not at.exception
    assert any(">Mock 1<" in m.value for m in at.markdown if "page-title" in m.value)


def test_top_bar_buttons_switch_pages(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01")])
    at = _app()
    at.button(key="nav_views/mocks.py").click().run()
    assert not at.exception
    assert any("All mocks" in m.value for m in at.markdown if "page-title" in m.value)
    at.button(key="nav_views/add.py").click().run()
    assert any("Add a mock" in m.value for m in at.markdown if "page-title" in m.value)
    at.button(key="nav_views/dashboard.py").click().run()
    assert any(">Dashboard<" in m.value for m in at.markdown if "page-title" in m.value)


def test_dashboard_shows_the_gap_to_the_target(record_file, parsed):
    _seed(record_file, parsed, [("Mock 1", "2026-10-01")])  # scores 85
    at = _app()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Marks to 165"] == "80"
