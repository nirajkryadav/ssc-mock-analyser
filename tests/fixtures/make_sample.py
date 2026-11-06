"""Build a synthetic SSC CGL response-sheet PDF that mirrors the real layout.

The real sheets the candidate downloads are screen captures (one bitmap per page,
no text layer), which no text parser can read. This generator reproduces the same
vector layout a browser "Print -> Save as PDF" produces: positioned text, option
swatch rects in the real SSC colours, blue chrome, grey "Not Answered" bars and
the repeated watermark number.

    python tests/fixtures/make_sample.py

writes sample_response_sheet.pdf and sample_ground_truth.json next to this file.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent

GREEN, RED, YELLOW, GREY, BLUE = "#008000", "#FF0000", "#FFFF00", "#808080", "#0079FE"
WATERMARK = "2261204189223"

PAGE_W, PAGE_H = letter
LEFT, RIGHT = 65.0, 560.0
SWATCH_W = 30.0
OPT_X = LEFT + SWATCH_W + 8

PARTS = [
    ("PART-A", "General Intelligence and Reasoning", 1, 25),
    ("PART-B", "General Awareness", 26, 50),
    ("PART-C", "Quantitative Aptitude", 51, 75),
    ("PART-D", "English Comprehension", 76, 100),
]

# Four plausible questions per part, cycled. Each is (text, [4 options]).
BANK = {
    "PART-A": [
        ('In a certain code language, "PEACE" is written as "MBXZB". How will "SMILE" be '
         'written in the same code language?', ["PJFIB", "PIFJB", "PIJFB", "PJIBF"]),
        ("Select the option that is related to the third term in the same way as the second "
         "term is related to the first term.", ["Bird : Nest", "Dog : Kennel", "Horse : Herd", "Fish : Shoal"]),
        ("A is the brother of B. B is the daughter of C. How is A related to C?",
         ["Son", "Nephew", "Father", "Brother"]),
        ("Walking towards north, Rahul turns left after 3 km, then walks 4 km. What is the "
         "shortest distance from his starting point?", ["5 km", "7 km", "6 km", "4 km"]),
    ],
    "PART-B": [
        ("Through which group of Indian states does the River Kaveri flow?",
         ["Madhya Pradesh, Maharashtra, and Gujarat", "Bihar, Odisha, and West Bengal",
          "Madhya Pradesh, Gujarat, and Rajasthan", "Karnataka, Kerala, and Tamil Nadu"]),
        ("Which Article of the Constitution of India deals with the Right to Equality?",
         ["Article 14", "Article 19", "Article 21", "Article 32"]),
        ("The Dronacharya Award is given in the field of which sport role?",
         ["Coaching", "Umpiring", "Commentary", "Administration"]),
        ("The Union Budget of India is presented under which Article of the Constitution?",
         ["Article 112", "Article 110", "Article 109", "Article 115"]),
    ],
    "PART-C": [
        ("The ratio of two numbers is 3 : 5 and their sum is 64. Find the smaller number.",
         ["24", "40", "28", "32"]),
        ("A shopkeeper sells an article at a profit of 20%. If the cost price is Rs. 450, "
         "find the selling price.", ["Rs. 540", "Rs. 520", "Rs. 560", "Rs. 500"]),
        ("Find the simple interest on Rs. 8000 at 5% per annum for 3 years.",
         ["Rs. 1200", "Rs. 1500", "Rs. 1000", "Rs. 1250"]),
        ("The area of a circle whose radius is 14 cm is (take pi = 22/7):",
         ["616 sq cm", "588 sq cm", "644 sq cm", "532 sq cm"]),
    ],
    "PART-D": [
        ("Select the most appropriate synonym of the given word: ABUNDANT",
         ["Plentiful", "Scarce", "Meagre", "Sparse"]),
        ("Select the most appropriate antonym of the given word: CONCEAL",
         ["Reveal", "Hide", "Cover", "Mask"]),
        ('Select the correct meaning of the given idiom: "To let the cat out of the bag"',
         ["To reveal a secret", "To buy a pet", "To escape danger", "To waste money"]),
        ("Select the segment of the sentence that contains a grammatical error: "
         "He have been working here since 2019.",
         ["He have been", "working here", "since 2019", "No error"]),
    ],
}


def _watermarks(c: canvas.Canvas) -> None:
    c.saveState()
    c.setFont("Helvetica", 11)
    c.setFillColor(HexColor("#E8E8E8"))
    for row in range(10):
        for col in range(4):
            c.drawString(70 + col * 140, 90 + row * 72, WATERMARK)
    c.restoreState()


def _header(c: canvas.Canvas) -> None:
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 48, "SSC ONLINE EXAMINATION")
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 62, "CANDIDATE RESPONSE SHEET/GRIEVANCE SYSTEM")
    fields = [
        ("Roll No.", "2261204189223"),
        ("Candidate Name", "ANJALI YADAV"),
        ("Exam Level", "Combined Graduate Level Examination, 2026"),
        ("Test Date", "28 Oct 2026"),
        ("Test Time and Shift", "05:00 PM (Shift-3)"),
        ("Centre Name", "AADARSH PARIKSHA KENDRA - NOIDA - SECTOR 80 ( NR )"),
    ]
    y = PAGE_H - 92
    for label, value in fields:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(LEFT + 10, y, label)
        c.drawString(LEFT + 150, y, ":")
        c.setFont("Helvetica", 8)
        c.drawString(LEFT + 162, y, value)
        y -= 15
    # legend, exactly as the sheet prints it
    legend = [(GREEN, "Correct Option selected"), (RED, "Wrong Option selected"),
              (YELLOW, "Correct Option"), (GREY, "Not Answered")]
    x = LEFT + 10
    for colour, label in legend:
        w = c.stringWidth(label, "Helvetica-Bold", 7) + 6
        c.setFillColor(HexColor(colour))
        c.rect(x, y - 2, w, 11, stroke=0, fill=1)
        c.setFillColor(HexColor("#000000"))
        c.setFont("Helvetica-Bold", 7)
        c.drawString(x + 3, y + 1, label)
        x += w + 4
    return y - 26


def _button(c: canvas.Canvas, x, y, label) -> None:
    w = c.stringWidth(label, "Helvetica-Bold", 7) + 14
    c.setFillColor(HexColor(BLUE))
    c.rect(x, y, w, 13, stroke=0, fill=1)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("Helvetica-Bold", 7)
    c.drawString(x + 7, y + 4, label)


def _wrap(c: canvas.Canvas, text, font, size, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if c.stringWidth(trial, font, size) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _block_height(c, q_text, options):
    h = 20  # challenge button strip
    h += 14 * len(_wrap(c, q_text, "Helvetica", 8.5, RIGHT - OPT_X - 10)) + 10
    for opt in options:
        h += 14 * len(_wrap(c, opt, "Helvetica", 8.5, RIGHT - OPT_X - 10)) + 12
    return h + 16


def _draw_block(c, y, q_no, q_text, options, selected, correct, status):
    """Draw one question block with its top edge at y. Returns the new y."""
    top = y
    _button(c, RIGHT - 150, y - 16, f"Click Here to Challenge Question No.: {q_no}")
    y -= 24

    c.setFont("Helvetica", 8)
    c.setFillColor(HexColor("#000000"))
    c.drawString(LEFT + 4, y - 10, f"Q.No: {q_no}")
    for line in _wrap(c, q_text, "Helvetica", 8.5, RIGHT - OPT_X - 10):
        c.setFont("Helvetica", 8.5)
        c.drawString(OPT_X, y - 10, line)
        y -= 14
    y -= 8
    c.setStrokeColor(HexColor("#CCCCCC"))
    c.line(LEFT, y, RIGHT, y)

    for idx, opt in enumerate(options):
        lines = _wrap(c, opt, "Helvetica", 8.5, RIGHT - OPT_X - 10)
        row_h = 14 * len(lines) + 12
        colour = None
        if status == "correct" and idx == selected:
            colour = GREEN
        elif status == "wrong" and idx == selected:
            colour = RED
        elif status in ("wrong", "skipped") and idx == correct:
            colour = YELLOW
        if colour:
            c.setFillColor(HexColor(colour))
            c.rect(LEFT, y - row_h, SWATCH_W, row_h, stroke=0, fill=1)
        c.setFillColor(HexColor("#000000"))
        yy = y - 12
        for line in lines:
            c.setFont("Helvetica", 8.5)
            c.drawString(OPT_X, yy, line)
            yy -= 14
        y -= row_h
        c.setStrokeColor(HexColor("#CCCCCC"))
        c.line(LEFT, y, RIGHT, y)

    if status == "skipped":
        c.setFillColor(HexColor(GREY))
        c.rect(LEFT, y - 13, RIGHT - LEFT, 13, stroke=0, fill=1)
        c.setFillColor(HexColor("#000000"))
        c.setFont("Helvetica-Bold", 7)
        c.drawString(LEFT + 4, y - 9, "Not Answered")
        y -= 13

    c.setStrokeColor(HexColor("#888888"))
    c.rect(LEFT, y, RIGHT - LEFT, top - y, stroke=1, fill=0)
    return y - 18


def make_truth(seed: int = 7) -> list[dict]:
    """The answer key the renderer will draw, decided before anything is drawn."""
    rng = random.Random(seed)
    truth = []
    for part, _name, first_q, last_q in PARTS:
        bank = BANK[part]
        for q_no in range(first_q, last_q + 1):
            q_text, options = bank[(q_no - first_q) % len(bank)]
            correct = rng.randrange(4)
            roll = rng.random()
            if roll < 0.18:
                status, selected = "skipped", None
            elif roll < 0.50:
                status = "wrong"
                selected = rng.choice([i for i in range(4) if i != correct])
            else:
                status, selected = "correct", correct
            truth.append({"q_no": q_no, "part": part, "status": status,
                          "selected_index": selected, "correct_index": correct,
                          "question_text": q_text, "options": list(options)})
    return truth


def render(truth: list[dict], out_pdf: Path, parts: list[str] | None = None) -> Path:
    """Draw the given questions as a response-sheet PDF."""
    parts = parts or [p[0] for p in PARTS]
    c = canvas.Canvas(str(out_pdf), pagesize=letter)
    _watermarks(c)
    y = _header(c)
    for part, name, _first, _last in PARTS:
        if part not in parts:
            continue
        rows = [t for t in truth if t["part"] == part]
        if y < 320:
            c.showPage()
            _watermarks(c)
            y = PAGE_H - 60
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(HexColor("#000000"))
        c.drawCentredString(PAGE_W / 2, y - 14, f"{part} ({name})")
        y -= 34
        for t in rows:
            need = _block_height(c, t["question_text"], t["options"])
            need += 13 if t["status"] == "skipped" else 0
            if y - need < 60:
                c.showPage()
                _watermarks(c)
                y = PAGE_H - 60
            y = _draw_block(c, y, t["q_no"], t["question_text"], t["options"],
                            t["selected_index"], t["correct_index"], t["status"])
    c.save()
    return out_pdf


def rasterise(pdf_path: Path, out_pdf: Path, dpi: int = 150) -> Path:
    """Flatten a PDF to one bitmap per page.

    This is what the screen-capture tools candidates use actually produce: the
    layout survives, the text layer does not. It is the input the image path has
    to cope with.
    """
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        images = [page.to_image(resolution=dpi).original.convert("RGB")
                  for page in pdf.pages]
    images[0].save(out_pdf, save_all=True, append_images=images[1:],
                   resolution=dpi)
    return out_pdf


def build(seed: int = 7, out_dir: Path | None = None, split_parts: bool = True):
    """Write the combined sheet, the four per-part sheets and the answer key."""
    out_dir = out_dir or HERE
    out_dir.mkdir(parents=True, exist_ok=True)
    truth = make_truth(seed)
    combined = render(truth, out_dir / "sample_response_sheet.pdf")
    written = [combined]
    if split_parts:
        for part, _name, _f, _l in PARTS:
            written.append(render(truth, out_dir / f"sample_{part.lower()}.pdf", parts=[part]))
    written.append(rasterise(combined, out_dir / "sample_scanned.pdf"))
    key = out_dir / "sample_ground_truth.json"
    key.write_text(json.dumps(truth, indent=1))
    counts = {s: sum(1 for t in truth if t["status"] == s) for s in ("correct", "wrong", "skipped")}
    print(f"wrote {len(written)} PDF(s) and {key.name} — {len(truth)} questions {counts}")
    return written, key


if __name__ == "__main__":
    build()
