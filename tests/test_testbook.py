"""Testbook solution pages: the pixel and layout rules the reader relies on."""
from PIL import Image, ImageDraw

import testbook as T


def test_recognises_a_testbook_page():
    pages = [{"lines": [{"text": f"Q. {n}) Some question Marks: 2"} for n in range(1, 5)]}]
    assert T.looks_like(pages)
    assert not T.looks_like([{"lines": [{"text": "Q.No: 1 Some question"}]}])


def test_question_labels_tolerate_ocr_noise():
    for text in ("Q. 1) Marks: 2", "Q.12)| Marks: 2", "Q. 25) |f # = x", "Q,7) x"):
        assert T.Q_RE.match(text), text
    assert not T.Q_RE.match("Click Here to Challenge Question No.: 51")


def test_marks_text_gives_the_result():
    assert T._marks_from_text("Q. 1) Marks: 2") == "correct"
    assert T._marks_from_text("Q. 2) Marks: -0.5") == "wrong"
    assert T._marks_from_text("Q. 3) Marks: O") == "skipped"  # OCR reads 0 as O


def test_wrapped_options_stay_one_row():
    # option A wraps over three lines (~11pt apart); the rows are ~18pt apart
    assert T.option_rows([592, 603, 614, 632, 645]) == [592, 632]
    assert T.option_rows([125, 147]) == [125, 147]


def _page(scale=2.0):
    img = Image.new("RGB", (int(612 * scale), int(200 * scale)), "white")
    d = ImageDraw.Draw(img)
    s = lambda *v: [x * scale for x in v]  # noqa: E731
    d.rounded_rectangle(s(455, 20, 495, 32), radius=6, fill=T.PILLS["wrong"])
    d.text(s(460, 22), "Marks: -0.5", fill=(231, 0, 12))
    d.ellipse(s(132, 60, 139, 67), fill=T.CIRCLES["your_wrong"])
    d.text(s(134, 61), "A", fill="white")
    d.ellipse(s(317, 60, 324, 67), fill=T.CIRCLES["correct_option"])
    d.text(s(319, 61), "B", fill="white")
    return img, scale


def test_pill_colour_on_the_label_row_gives_the_result():
    img, scale = _page()
    assert T.pill_status(img, scale, 18, 34, 360, 550) == "wrong"
    assert T.pill_status(img, scale, 100, 120, 360, 550) is None  # blank row


def test_letter_circles_are_found_with_their_letters_printed_on_them():
    img, scale = _page()
    found = {(c["kind"], round(c["x0"])) for c in T.find_circles(img, scale)}
    assert ("your_wrong", 132) in found and ("correct_option", 317) in found
