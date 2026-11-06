"""Parser tests: colour mapping, block segmentation and whole-sheet validation."""
import pytest

import parser
from parser import ParseError, classify_colour, normalise_colour


COLOURS = parser.load_config()["colours"]


# --------------------------------------------------------------- colour -> status

@pytest.mark.parametrize("raw, expected", [
    ((0.0, 0.502, 0.0), "selected_correct"),     # green, RGB floats
    ((1.0, 0.0, 0.0), "selected_wrong"),         # red
    ((1.0, 1.0, 0.0), "correct_option"),         # yellow
    ((0.502, 0.502, 0.502), "not_answered"),     # grey
    ((0, 128, 0), "selected_correct"),           # same green as 8-bit ints
    ((255, 255, 0), "correct_option"),
    ((0.502,), "not_answered"),                  # single-channel greyscale
    ((1.0, 1.0, 1.0), None),                     # white cell: no status
    ((0.0, 0.0, 0.0), None),                     # black text fill
    ((0.0, 0.475, 0.996), None),                 # blue button, explicitly ignored
    (None, None),
])
def test_classify_colour(raw, expected):
    assert classify_colour(raw, COLOURS) == expected


def test_jpeg_noise_still_maps_to_the_right_status():
    """Sheets re-encoded on the way to PDF shift colours by a few counts."""
    assert classify_colour((0.0, 0.503, 0.004), COLOURS) == "selected_correct"
    assert classify_colour((0.992, 0.0, 0.0), COLOURS) == "selected_wrong"
    assert classify_colour((0.996, 0.996, 0.0), COLOURS) == "correct_option"


def test_green_and_red_are_never_confused():
    """The two selected-option colours must not fall inside each other's tolerance."""
    green = normalise_colour((0.0, 0.502, 0.0))
    red = normalise_colour((1.0, 0.0, 0.0))
    assert max(abs(a - b) for a, b in zip(green, red)) > COLOURS["tolerance"]


def test_normalise_colour_handles_cmyk():
    assert normalise_colour((0.0, 0.0, 0.0, 0.0)) == (255, 255, 255)
    assert normalise_colour((0.0, 0.0, 0.0, 1.0)) == (0, 0, 0)


# ------------------------------------------------------------------- whole sheet

def test_sheet_has_exactly_100_questions(parsed):
    assert len(parsed.questions) == 100
    assert [q.q_no for q in parsed.questions] == list(range(1, 101))


def test_sections_are_25_each(parsed, config):
    for sec in config["sections"]:
        got = [q for q in parsed.questions if q.section == sec["key"]]
        assert len(got) == 25, f"{sec['part']} has {len(got)} questions"


def test_every_question_has_exactly_one_status(parsed):
    for q in parsed.questions:
        assert q.status in ("correct", "wrong", "skipped")


def test_validation_passes_on_sample(parsed, config):
    assert parser.validate(parsed, config, strict=False) == []


def test_header_metadata_is_read(parsed):
    assert parsed.meta.roll_no == "2261204189223"
    assert parsed.meta.candidate_name == "ANJALI YADAV"
    assert parsed.meta.test_date == "28 Oct 2026"


def test_watermark_is_not_mistaken_for_content(parsed):
    """The roll number is repeated across every page as a watermark."""
    for q in parsed.questions:
        assert "2261204189223" not in q.question_text
        assert all("2261204189223" not in opt for opt in q.options)


def test_records_match_the_known_answer_key(parsed, ground_truth):
    for q in parsed.questions:
        want = ground_truth[q.q_no]
        assert q.status == want["status"], f"Q{q.q_no} status"
        assert q.selected_index == want["selected_index"], f"Q{q.q_no} selected"
        assert q.correct_index == want["correct_index"], f"Q{q.q_no} correct"
        assert q.options == want["options"], f"Q{q.q_no} options"
        assert q.question_text == want["question_text"], f"Q{q.q_no} text"


def test_skipped_questions_take_the_yellow_option_as_correct(parsed):
    skipped = [q for q in parsed.questions if q.status == "skipped"]
    assert skipped, "fixture should contain skipped questions"
    for q in skipped:
        assert q.selected_index is None
        assert q.correct_index is not None


def test_wrong_questions_know_both_options(parsed):
    wrong = [q for q in parsed.questions if q.status == "wrong"]
    assert wrong
    for q in wrong:
        assert q.selected_index is not None
        assert q.correct_index is not None
        assert q.selected_index != q.correct_index


def test_every_question_has_four_options(parsed):
    for q in parsed.questions:
        assert len(q.options) == 4, f"Q{q.q_no} has {len(q.options)} options"


# ------------------------------------------------------------- four part uploads

def test_four_part_files_merge_into_one_sheet(part_pdfs, config, ground_truth):
    """SSC serves one PDF per PART, so a full mock usually arrives as four files."""
    assert len(part_pdfs) == 4
    merged = parser.parse_many(part_pdfs, config)
    assert len(merged.questions) == 100
    assert parser.validate(merged, config, strict=False) == []
    for q in merged.questions:
        assert q.status == ground_truth[q.q_no]["status"]


def test_a_single_part_fails_validation_loudly(part_pdfs, config):
    one = parser.parse_pdf(part_pdfs[0], config)
    assert len(one.questions) == 25
    with pytest.raises(ParseError) as excinfo:
        parser.validate(one, config, strict=True)
    assert "expected 100 questions" in str(excinfo.value)


def test_parse_accepts_bytes(sample_pdf, config):
    result = parser.parse_pdf(sample_pdf.read_bytes(), config)
    assert len(result.questions) == 100


# ------------------------------------------------------------------ bad input

def test_a_pdf_without_questions_is_rejected(tmp_path, config):
    from reportlab.pdfgen import canvas
    path = tmp_path / "not_a_sheet.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(72, 720, "This is an electricity bill, not a response sheet.")
    c.save()
    with pytest.raises(ParseError) as excinfo:
        parser.parse_pdf(path, config)
    assert "Q.No" in str(excinfo.value)


def test_an_image_only_pdf_explains_itself(tmp_path, config):
    """The sheets candidates save with a screen-capture tool have no text layer."""
    from PIL import Image
    from reportlab.pdfgen import canvas
    img = tmp_path / "page.png"
    Image.new("RGB", (600, 800), "white").save(img)
    path = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(path))
    c.drawImage(str(img), 0, 0, width=600, height=800)
    c.save()
    with pytest.raises(ParseError) as excinfo:
        parser.parse_pdf(path, config)
    assert "only page images" in str(excinfo.value)


def test_validation_report_states_pass(parsed, config):
    report = parser.validation_report(parsed, config)
    assert "PASSED" in report
    assert "25 / 25" in report
