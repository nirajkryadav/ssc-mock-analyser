"""The image path: reading a sheet that has no text layer.

A screen-capture PDF is one bitmap per page. Colours and table rules are
recovered from the pixels and the words from OCR, then everything joins the
same pipeline a text PDF uses. OCR is machine-dependent, so the tests that
need it skip where no backend exists; the pixel tests always run.
"""
import json

import numpy as np
import pytest
from PIL import Image

import image_source
import parser
from parser import ParseError

COLOURS = parser.load_config()["colours"]
HAS_OCR = bool(image_source.available_backends())
needs_ocr = pytest.mark.skipif(not HAS_OCR, reason="no OCR backend on this machine")


# -------------------------------------------------------------- pixel detection

def _canvas(blocks, size=(600, 400)):
    img = Image.new("RGB", size, "white")
    px = img.load()
    for colour, (x0, y0, x1, y1) in blocks:
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[x, y] = colour
    return img


def test_detects_each_status_colour():
    img = _canvas([
        ((0x00, 0x80, 0x00), (10, 10, 60, 40)),
        ((0xFF, 0x00, 0x00), (10, 60, 60, 90)),
        ((0xFF, 0xFF, 0x00), (10, 110, 60, 140)),
        ((0x80, 0x80, 0x80), (10, 160, 580, 180)),   # full-width "Not Answered"
    ])
    found = {f["status"] for f in image_source.detect_fills(img, COLOURS, 1.0)}
    assert found == {"selected_correct", "selected_wrong", "correct_option",
                     "not_answered"}


def test_a_narrow_grey_line_is_a_border_not_a_skip():
    """Table borders are grey too. Only a wide grey bar means 'Not Answered'."""
    img = _canvas([((0x80, 0x80, 0x80), (10, 50, 90, 70))])
    assert image_source.detect_fills(img, COLOURS, 1.0) == []


def test_swatch_geometry_is_reported_in_pdf_points():
    """A 2x render must report half-size coordinates, matching the text path."""
    img = _canvas([((0x00, 0x80, 0x00), (20, 40, 80, 100))])
    fill = image_source.detect_fills(img, COLOURS, 2.0)[0]
    assert fill["top"] == pytest.approx(20, abs=1)
    assert fill["x0"] == pytest.approx(10, abs=1)


def test_the_header_legend_is_not_mistaken_for_answers():
    """Three status colours on one row is the colour key, never a question."""
    img = _canvas([
        ((0x00, 0x80, 0x00), (10, 10, 60, 30)),
        ((0xFF, 0x00, 0x00), (70, 10, 120, 30)),
        ((0xFF, 0xFF, 0x00), (130, 10, 180, 30)),
        ((0x00, 0x80, 0x00), (10, 200, 60, 230)),   # a real answer, further down
    ])
    fills = image_source.detect_fills(img, COLOURS, 1.0)
    assert len(fills) == 1
    assert fills[0]["top"] == pytest.approx(200, abs=1)


def test_detects_table_rules_but_not_text():
    img = Image.new("RGB", (600, 200), "white")
    px = img.load()
    for x in range(20, 580):            # a rule: one long unbroken run
        px[x, 100] = (0xCC, 0xCC, 0xCC)
    for x in range(20, 580, 4):         # "text": the same ink, but sparse
        px[x, 50] = (0x00, 0x00, 0x00)
    rules = image_source.detect_rules(img, 1.0)
    assert any(abs(r - 100) <= 1 for r in rules)
    assert not any(abs(r - 50) <= 1 for r in rules)


def test_faint_watermark_words_are_dropped():
    """Printed text covers its box in dark ink; the watermark covers none."""
    img = Image.new("RGB", (300, 100), "white")
    px = img.load()
    for x in range(10, 60):             # real text: dark strokes
        for y in range(20, 32, 3):
            px[x, y] = (0x10, 0x10, 0x10)
    for x in range(150, 260):           # watermark: pale grey wash
        for y in range(20, 40):
            px[x, y] = (0xE8, 0xE8, 0xE8)
    words = [{"text": "Real", "x0": 10, "x1": 60, "top": 18, "bottom": 34},
             {"text": "2261204189223", "x0": 150, "x1": 260, "top": 18, "bottom": 42}]
    kept = image_source.drop_faint_words(img, words, 1.0)
    assert [w["text"] for w in kept] == ["Real"]


def test_luminance_does_not_overflow_on_white():
    """The weighted RGB sum exceeds int16 well before white."""
    img = Image.new("RGB", (40, 20), "white")
    words = [{"text": "x", "x0": 0, "x1": 40, "top": 0, "bottom": 20}]
    assert image_source.drop_faint_words(img, words, 1.0) == []


# ------------------------------------------------------------- numbering repair

def test_misread_question_number_is_repaired_from_position():
    blocks = [{"q_no": n} for n in range(1, 101)]
    blocks[99]["q_no"] = 10          # OCR reads "Q.No: 100" as "Q.No: 10"
    notes = parser.repair_numbering(blocks)
    assert blocks[99]["q_no"] == 100
    assert "corrected to 100" in notes[0]


def test_numbering_repair_respects_a_part_file_offset():
    blocks = [{"q_no": n} for n in range(26, 51)]
    blocks[4]["q_no"] = 3
    parser.repair_numbering(blocks)
    assert blocks[4]["q_no"] == 30


def test_numbering_is_left_alone_when_most_blocks_disagree():
    """Wholesale disagreement means a wrong assumption, not an OCR slip."""
    blocks = [{"q_no": n} for n in (5, 19, 2, 44, 7, 31, 12, 88, 3, 60)]
    before = [b["q_no"] for b in blocks]
    assert parser.repair_numbering(blocks) == []
    assert [b["q_no"] for b in blocks] == before


# --------------------------------------------------------------- whole document

@pytest.fixture(scope="module")
def scanned(scanned_pdf):
    if not HAS_OCR:
        pytest.skip("no OCR backend on this machine")
    return parser.parse_pdf(scanned_pdf)


@needs_ocr
def test_an_image_only_sheet_is_read_from_its_pixels(scanned):
    assert scanned.source == "image"
    assert scanned.ocr_backend
    assert len(scanned.questions) == 100


@needs_ocr
def test_image_path_validates(scanned, config):
    assert parser.validate(scanned, config, strict=False) == []


@needs_ocr
def test_image_path_recovers_every_status(scanned, ground_truth):
    """Status is carried by colour, so it should survive OCR entirely."""
    wrong = [q.q_no for q in scanned.questions
             if q.status != ground_truth[q.q_no]["status"]]
    assert wrong == []


@needs_ocr
def test_image_path_recovers_the_option_indices(scanned, ground_truth):
    wrong = [q.q_no for q in scanned.questions
             if (q.selected_index, q.correct_index)
             != (ground_truth[q.q_no]["selected_index"],
                 ground_truth[q.q_no]["correct_index"])]
    assert wrong == []


@needs_ocr
def test_image_path_reads_most_option_text(scanned, ground_truth):
    """OCR will not be perfect on every glyph; it should be close."""
    exact = sum(1 for q in scanned.questions
                if q.options == ground_truth[q.q_no]["options"])
    assert exact >= 85, f"only {exact}/100 option sets matched exactly"


@needs_ocr
def test_image_path_reads_the_header(scanned):
    assert scanned.meta.candidate_name == "ANJALI YADAV"
    assert scanned.meta.roll_no == "2261204189223"


@needs_ocr
def test_watermark_never_becomes_an_option(scanned):
    for q in scanned.questions:
        assert all("2261204189" not in opt for opt in q.options), f"Q{q.q_no}"


def test_image_fallback_can_be_refused(scanned_pdf, config):
    with pytest.raises(ParseError) as excinfo:
        parser.parse_pdf(scanned_pdf, config, allow_image_fallback=False)
    assert "only page images" in str(excinfo.value)


def test_text_pdf_still_takes_the_text_path(sample_pdf, config):
    assert parser.parse_pdf(sample_pdf, config).source == "text"


# ------------------------------------------------- low-resolution screenshots

def _as_picture_pdf(path, scale):
    """Rasterise a PDF and wrap the pictures in a new PDF with no text layer."""
    import io
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path))
    imgs = [doc[i].render(scale=scale).to_pil().convert("RGB") for i in range(len(doc))]
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:], resolution=72 * scale)
    return buf.getvalue()


def test_a_missed_option_row_does_not_shift_the_answer():
    """OCR can drop a short option on a small scan; the answer must stay in its row."""
    import parser as P
    rules = [100.0, 100.7, 125.0, 125.6, 150.0, 175.0, 200.0, 200.7, 201.4]
    anchor = {"top": 80.0}
    read = [{"text": "24", "top": 105, "bottom": 115, "mid": 110},
            {"text": "40", "top": 130, "bottom": 140, "mid": 135},
            {"text": "28", "top": 155, "bottom": 165, "mid": 160}]
    slots = P._fill_missing_options(read, anchor, rules)
    assert [o["text"] for o in slots] == ["24", "40", "28", ""]
    assert 175 <= slots[3]["mid"] <= 200


def test_option_rows_that_do_not_fit_the_table_are_left_alone():
    import parser as P
    read = [{"text": "x", "top": 300, "bottom": 310, "mid": 305}]
    assert P._fill_missing_options(read, {"top": 80.0}, [100.0, 125.0]) == read


def test_small_screenshots_are_flagged(fixtures_dir):
    import pdfplumber, io
    import parser as P
    small = _as_picture_pdf(fixtures_dir / "sample_part-a.pdf", 1.5)
    with pdfplumber.open(io.BytesIO(small)) as pdf:
        assert P._picture_width(pdf) < P.LOW_RES_PX
    with pdfplumber.open(fixtures_dir / "sample_scanned.pdf") as pdf:
        assert P._picture_width(pdf) >= P.LOW_RES_PX
