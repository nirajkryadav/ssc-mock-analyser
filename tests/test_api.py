"""API tests: the two endpoints, their contract, and how they fail."""
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="module")
def parsed_payload(request):
    """Parse the text fixture through the API and keep the records."""
    path = request.config.rootpath / "tests" / "fixtures" / "sample_response_sheet.pdf"
    with open(path, "rb") as fh:
        res = client.post("/api/parse", files={"file": ("sheet.pdf", fh, "application/pdf")})
    assert res.status_code == 200, res.text
    return res.json()


# ------------------------------------------------------------------- health

def test_health_reports_ocr_capability():
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert isinstance(body["ocr_backends"], list)
    assert body["max_upload_bytes"] > 0


# -------------------------------------------------------------------- parse

def test_parse_returns_every_question(parsed_payload):
    assert len(parsed_payload["questions"]) == 100
    assert parsed_payload["source"] == "text"


def test_parse_tags_topics(parsed_payload):
    assert all(q["topic"] for q in parsed_payload["questions"])


def test_parse_includes_readable_option_labels(parsed_payload):
    wrong = next(q for q in parsed_payload["questions"] if q["status"] == "wrong")
    assert wrong["selected_label"][1:3] == ". "
    assert wrong["correct_label"] != wrong["selected_label"]


def test_parse_reads_the_header(parsed_payload):
    assert parsed_payload["meta"]["candidate_name"] == "ANJALI YADAV"


def test_parse_rejects_an_empty_file():
    res = client.post("/api/parse", files={"file": ("x.pdf", b"", "application/pdf")})
    assert res.status_code == 400


def test_parse_rejects_a_file_that_is_not_a_sheet():
    res = client.post("/api/parse",
                      files={"file": ("x.pdf", b"not a pdf at all", "application/pdf")})
    assert res.status_code == 422
    assert "could not be read" in res.json()["detail"].lower()


def test_parse_accepts_one_part_file(request):
    """A single PART is 25 questions — valid input, just not a whole paper."""
    path = request.config.rootpath / "tests" / "fixtures" / "sample_part-a.pdf"
    with open(path, "rb") as fh:
        res = client.post("/api/parse", files={"file": ("a.pdf", fh, "application/pdf")})
    assert res.status_code == 200
    assert len(res.json()["questions"]) == 25


# ------------------------------------------------------------------ analyse

def _analyse(payload, **over):
    body = {"questions": payload["questions"], "meta": payload["meta"],
            "mock_name": "Mock 1", "mock_date": "2026-10-28"}
    body.update(over)
    res = client.post("/api/analyse", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_analyse_scores_the_paper(parsed_payload):
    out = _analyse(parsed_payload)
    assert out["validation"]["passed"] is True
    assert out["summary"]["score"] == 85.0
    assert out["summary"]["correct"] == 50
    assert len(out["sections"]) == 4


def test_analyse_honours_a_custom_marking_scheme(parsed_payload):
    out = _analyse(parsed_payload, marking={"correct": 1, "wrong": -0.25, "skipped": 0})
    assert out["summary"]["score"] == pytest.approx(50 * 1 - 30 * 0.25)


def test_analyse_returns_a_seven_day_plan(parsed_payload):
    out = _analyse(parsed_payload)
    assert [d["day"] for d in out["plan"]] == list(range(1, 8))
    assert all(d["blocks"] for d in out["plan"])


def test_analyse_returns_downloadable_artefacts(parsed_payload):
    out = _analyse(parsed_payload)
    assert out["report_markdown"].startswith("# SSC CGL mock analysis")
    assert out["history_csv"].startswith("mock_date,mock_name,q_no")
    assert len(out["wrong_answers"]) == 30


def test_analyse_without_history_reports_no_trend(parsed_payload):
    assert _analyse(parsed_payload)["trend"]["available"] is False


def test_analyse_with_history_reports_a_trend(parsed_payload):
    earlier = _analyse(parsed_payload, mock_name="Mock 0", mock_date="2026-10-01")
    out = _analyse(parsed_payload, history_csv=earlier["history_csv"])
    assert out["trend"]["available"] is True
    assert out["trend"]["score_change"] == pytest.approx(0.0)


def test_analyse_ignores_a_bad_history_file_without_failing(parsed_payload):
    out = _analyse(parsed_payload, history_csv="a,b\n1,2\n")
    assert out["history_error"]
    assert out["summary"]["score"] == 85.0


def test_analyse_rejects_an_empty_body():
    assert client.post("/api/analyse", json={"questions": []}).status_code == 400


def test_analyse_reports_validation_problems_rather_than_failing(parsed_payload):
    """A partial upload still returns numbers, flagged as not validating."""
    out = _analyse({**parsed_payload, "questions": parsed_payload["questions"][:25]})
    assert out["validation"]["passed"] is False
    assert any("expected 100" in p for p in out["validation"]["problems"])


def test_analyse_retags_topics_it_is_given(parsed_payload):
    """Topic is derived server-side, so a client cannot inject a bogus one."""
    tampered = json.loads(json.dumps(parsed_payload["questions"]))
    for q in tampered:
        q["topic"] = "Nonsense"
    out = _analyse({**parsed_payload, "questions": tampered})
    assert all(t["label"] != "Nonsense" for t in out["topics"])


def test_cors_is_restricted_to_configured_origins():
    res = client.options("/api/analyse", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert res.headers.get("access-control-allow-origin") != "https://evil.example"


# ------------------------------------------------------------------ records

@pytest.fixture
def empty_store(monkeypatch):
    import records
    from api import main
    fresh = records.MemoryStore()
    monkeypatch.setattr(main, "STORE", fresh)
    return fresh


def test_saved_mock_is_listed_with_its_summary(parsed_payload, empty_store):
    out = _analyse(parsed_payload, save=True)
    assert out["record_id"] == "mock-1"
    assert out["replaced_existing"] is False
    records = client.get("/api/records").json()["records"]
    assert [r["id"] for r in records] == ["mock-1"]
    assert records[0]["summary"]["score"] == 85.0
    assert len(records[0]["sections"]) == 4
    assert "questions" not in records[0]


def test_unsaved_analysis_is_not_stored(parsed_payload, empty_store):
    _analyse(parsed_payload)
    assert client.get("/api/records").json()["records"] == []


def test_saving_compares_against_stored_mocks(parsed_payload, empty_store):
    _analyse(parsed_payload, save=True, mock_name="Mock 0", mock_date="2026-10-01")
    out = _analyse(parsed_payload, save=True)
    assert out["trend"]["available"] is True
    assert [m["mock_name"] for m in out["mocks"]] == ["Mock 0", "Mock 1"]


def test_saving_the_same_name_replaces_the_record(parsed_payload, empty_store):
    _analyse(parsed_payload, save=True)
    out = _analyse(parsed_payload, save=True, mock_date="2026-10-30")
    assert out["replaced_existing"] is True
    records = client.get("/api/records").json()["records"]
    assert len(records) == 1 and records[0]["mock_date"] == "2026-10-30"


def test_stored_mock_rebuilds_the_full_report(parsed_payload, empty_store):
    first = _analyse(parsed_payload, save=True)
    again = client.get("/api/records/mock-1").json()
    assert again["summary"] == first["summary"]
    assert again["report_markdown"] == first["report_markdown"]
    assert len(again["wrong_answers"]) == 30


def test_history_export_holds_every_stored_mock(parsed_payload, empty_store):
    _analyse(parsed_payload, save=True, mock_name="Mock 0", mock_date="2026-10-01")
    _analyse(parsed_payload, save=True)
    res = client.get("/api/records/history.csv")
    assert res.status_code == 200
    lines = res.text.strip().splitlines()
    assert lines[0].startswith("mock_date,mock_name,q_no")
    assert len(lines) == 1 + 200


def test_deleting_a_mock_removes_it(parsed_payload, empty_store):
    _analyse(parsed_payload, save=True)
    assert client.delete("/api/records/mock-1").status_code == 200
    assert client.get("/api/records").json()["records"] == []
    assert client.get("/api/records/mock-1").status_code == 404
    assert client.delete("/api/records/mock-1").status_code == 404


def test_file_store_survives_a_restart(tmp_path):
    import records
    path = tmp_path / "records.json"
    records.FileStore(path).put("a", {"id": "a", "mock_name": "A"})
    assert records.FileStore(path).get("a")["mock_name"] == "A"
    assert records.record_id("  Mock #3 (Oct) ") == "mock-3-oct"
