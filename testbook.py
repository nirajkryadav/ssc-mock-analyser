"""Reader for Testbook mock-test solution pages.

Testbook's "solution" view lists every question as:

    Q. 7)  question text ...                         [Marks: 2]   <- coloured pill
    (A) option   (B) option
    (C) option   (D) option                                        <- 2 x 2 grid

The pill says how the question went: green "Marks: 2" (right), red
"Marks: -0.5" (wrong), grey "Marks: 0" (not attempted). In the grid the
correct option's letter circle is green, and the option you picked, if it was
wrong, is red. Sections start with "Section: <name>" and number their
questions from 1 again.

Questions are found from their "Q. n)" labels (OCR finds those reliably; the
pills alone are not distinctive enough, other page furniture shares the grey).
What decides the score is read from pixels next to each label: the pill's
colour on the label's row, and which letter circle is green or red. OCR
supplies the wording, the section headers, and "My Total Marks", which is
reported so it can be compared with the app's own total.
"""
from __future__ import annotations

import difflib
import io
import re
from typing import Any, Sequence

import numpy as np

import image_source

# Measured on a Testbook solution page (sRGB).
PILLS = {
    "correct": (219, 252, 231),   # green  "Marks: 2"
    "wrong": (255, 227, 226),     # red    "Marks: -0.5"
    "skipped": (243, 244, 246),   # grey   "Marks: 0"
}
CIRCLES = {
    "correct_option": (0, 201, 79),
    "your_wrong": (255, 101, 103),
}

Q_RE = re.compile(r"^\s*Q\s*[.,]?\s*(\d{1,3})\s*[)\]|]")
SECTION_RE = re.compile(r"^\s*Section\s*[:;.]?\s*(.+)$", re.I)
TOTAL_RE = re.compile(r"Total\s*Marks\s*[:;]?\s*(-?\d+(?:\.\d+)?)", re.I)
MARKS_TEXT_RE = re.compile(r"Marks\s*[:;]?\s*(-?\s*[\dOo](?:\.\d+)?)", re.I)
TAG_RE = re.compile(r"\s*[✓✔vVx×X/]?\s*(Corr?[ae]ct|Your\s*Answ?er)\b.*$", re.I)

SECTION_HINTS = {
    "reasoning": ("reasoning", "intelligence"),
    "general_awareness": ("awareness", "general knowledge", "gk"),
    "quant": ("quant", "aptitude", "math", "arithmetic"),
    "english": ("english", "comprehension", "language"),
}


def looks_like(pages: Sequence[dict[str, Any]]) -> bool:
    """True when the text has Testbook's "Q. n)" labels and "Marks:" lines."""
    text = [l["text"] for p in pages for l in p["lines"]]
    questions = sum(1 for t in text if Q_RE.match(t))
    marks = sum(1 for t in text if "Marks" in t)
    return questions >= 3 and marks >= 3


# ----------------------------------------------------------------------- pixels


def _close(mask: np.ndarray, gap: int) -> np.ndarray:
    """Fill horizontal gaps up to `gap` pixels: the letter printed in a circle
    punches a hole that would otherwise split it into slivers."""
    grown = mask.copy()
    for k in range(1, gap + 1):
        grown[:, k:] |= mask[:, :-k]
        grown[:, :-k] |= mask[:, k:]
    shrunk = grown.copy()
    for k in range(1, gap + 1):
        shrunk[:, k:] &= grown[:, :-k]
        shrunk[:, :-k] &= grown[:, k:]
    return shrunk


def find_circles(img, scale: float) -> list[dict[str, Any]]:
    """Green (correct option) and red (your wrong pick) letter circles, in points."""
    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    out = []
    for kind, rgb in CIRCLES.items():
        mask = np.abs(arr - np.array(rgb, dtype=np.int16)).max(axis=2) <= 16
        mask = _close(mask, max(1, round(2.5 * scale)))
        for b in image_source._components(mask, max(2, int(3.5 * scale)),
                                          max(2, int(3.5 * scale))):
            w, h = (b["x1"] - b["x0"]) / scale, (b["bottom"] - b["top"]) / scale
            if 3.5 <= w <= 11 and 3.5 <= h <= 11 and abs(w - h) <= 3:
                out.append({"kind": kind, "x0": b["x0"] / scale, "x1": b["x1"] / scale,
                            "top": b["top"] / scale, "bottom": b["bottom"] / scale,
                            "mid": (b["top"] + b["bottom"]) / 2 / scale})
    return out


def pill_status(img, scale: float, top: float, bottom: float, x0: float,
                x1: float) -> str | None:
    """Which pill colour fills the given strip (the right end of a label's row)."""
    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    y0, y1 = max(0, int(top * scale)), min(arr.shape[0], int(bottom * scale) + 1)
    region = arr[y0:y1, max(0, int(x0 * scale)):min(arr.shape[1], int(x1 * scale))]
    if not region.size:
        return None
    counts = {s: int((np.abs(region - np.array(rgb, dtype=np.int16)).max(axis=2) <= 5).sum())
              for s, rgb in PILLS.items()}
    status, n = max(counts.items(), key=lambda kv: kv[1])
    return status if n >= 40 * scale else None


def _marks_from_text(text: str) -> str | None:
    m = MARKS_TEXT_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace("O", "0").replace("o", "0")
    try:
        value = float(raw)
    except ValueError:
        return None
    return "correct" if value > 0 else "wrong" if value < 0 else "skipped"


# ------------------------------------------------------------------------ parse


def _section_for(name: str, sections: Sequence[dict]) -> dict | None:
    low = name.lower()
    for sec in sections:
        if any(h in low for h in SECTION_HINTS.get(sec["key"], ())):
            return sec
    names = [s["name"] for s in sections]
    hit = difflib.get_close_matches(name, names, n=1, cutoff=0.5)
    return next((s for s in sections if hit and s["name"] == hit[0]), None)


def option_rows(ys: Sequence[float], gap: float = 13.0) -> list[float]:
    """The two option rows: the last two clusters of option-row evidence.

    A long option wraps onto lines about 11pt apart, while the two rows of the
    grid are about 20pt apart, so lines closer than `gap` are one row. A row's
    position is its first line, which is where its letter circle sits."""
    clusters: list[list[float]] = []
    for y in sorted(ys):
        if clusters and y - clusters[-1][-1] <= gap:
            clusters[-1].append(y)
        else:
            clusters.append([y])
    return [c[0] for c in clusters[-2:]]


def parse(source: bytes, pages: Sequence[dict[str, Any]], config: dict[str, Any],
          images=None, scales=None, raw_words=None):
    """Questions from a Testbook solution PDF, as a parser.ParseResult."""
    import pdfplumber
    from parser import ParseResult, Question, SheetMeta

    sections = config["sections"]
    if images is None:
        with pdfplumber.open(io.BytesIO(source)) as pdf:
            images = [p.to_image(resolution=150).original for p in pdf.pages]
            widths = [p.width for p in pdf.pages]
        scales = [im.size[0] / w for im, w in zip(images, widths)]
    widths = [im.size[0] / s for im, s in zip(images, scales)]
    width = max(widths)

    # One long axis across pages, so a question split by a page break is whole.
    offsets, off = [], 0.0
    for p in pages:
        offsets.append(off)
        off += p["height"] + 1

    def page_of(y: float) -> int:
        return max(i for i, o in enumerate(offsets) if o <= y)

    lines, circles = [], []
    for i, page in enumerate(pages):
        d = offsets[i]
        for l in page["lines"]:
            lines.append({**l, "top": l["top"] + d, "bottom": l["bottom"] + d,
                          "mid": l["mid"] + d,
                          "words": [{**w, "top": w["top"] + d, "bottom": w["bottom"] + d}
                                    for w in l.get("words", [])]})
        circles += [{**c, "top": c["top"] + d, "bottom": c["bottom"] + d, "mid": c["mid"] + d}
                    for c in find_circles(images[i], scales[i])]
    lines.sort(key=lambda l: l["top"])

    labels = [l for l in lines if Q_RE.match(l["text"]) and l["x0"] < width * 0.3]
    if not labels:
        return ParseResult(questions=[], meta=SheetMeta(), warnings=["no Q. n) labels"],
                           source="image")
    label_x = sorted(l["x0"] for l in labels)[len(labels) // 2]
    headers = [(l["top"], m.group(1)) for l in lines if (m := SECTION_RE.match(l["text"]))]
    # "My Total Marks" is white on a coloured bar, which the faint-ink filter
    # drops, so look for it in the raw OCR words too.
    raw_text = [l["text"] for l in lines]
    if raw_words:
        from parser import group_words
        raw_text += [l["text"] for page in raw_words for l in group_words(page)]
    total = next((float(m.group(1)) for t in raw_text if (m := TOTAL_RE.search(t))), None)

    # Option columns: where green/red circles sit, split at the page's middle.
    left = sorted(c["x0"] for c in circles if c["x0"] < width * 0.45)
    right = sorted(c["x0"] for c in circles if c["x0"] >= width * 0.45)
    cols = (left[len(left) // 2] if left else width * 0.22,
            right[len(right) // 2] if right else width * 0.52)

    warnings: list[str] = []
    questions: list[Question] = []
    counts: dict[str, int] = {}
    for i, label in enumerate(labels):
        start = label["top"] - 3
        end = labels[i + 1]["top"] - 3 if i + 1 < len(labels) else off
        above = [name for top, name in headers if top < label["top"]]
        sec = (_section_for(above[-1], sections) if above else None) or \
            sections[min(len(questions) // 25, len(sections) - 1)]
        n = counts.get(sec["key"], 0) + 1
        printed = int(Q_RE.match(label["text"]).group(1))
        if n < printed <= min(n + 2, sec["last_q"] - sec["first_q"] + 1):
            # A label OCR missed (often one cut by a screenshot join). Keep the
            # numbering in step with the sheet and hold the question's place.
            for missing in range(n, printed):
                warnings.append(f"Q{sec['first_q'] + missing - 1} ({sec['name']} Q.{missing}) "
                                "couldn't be read from the page; counted as not attempted")
                questions.append(Question(
                    q_no=sec["first_q"] + missing - 1, section=sec["key"],
                    section_name=sec["name"], part=sec["part"],
                    question_text="(this question couldn't be read from the page)",
                    options=["", "", "", ""], selected_index=None, correct_index=None,
                    status="skipped"))
            n = printed
        counts[sec["key"]] = n
        q_no = sec["first_q"] + n - 1

        # Result: the pill's colour at the right end of the label's row.
        p = page_of(label["top"])
        status = pill_status(images[p], scales[p], label["top"] - offsets[p] - 2,
                             label["bottom"] - offsets[p] + 4, width * 0.6, width * 0.9)
        status = status or _marks_from_text(label["text"])
        if status is None:
            status = "skipped"
            warnings.append(f"Q{q_no}: couldn't read its marks; counted as not attempted")

        # Option rows: lines that start right of the label column (question text
        # starts at the label column), and the coloured circles. Last two rows.
        block = [l for l in lines if start <= l["top"] < end]
        marks = [c for c in circles if start <= c["mid"] < end]
        rows = option_rows([l["mid"] for l in block
                            if l["x0"] > label_x + 6 and l["top"] > label["bottom"]]
                           + [c["mid"] for c in marks])
        if len(rows) < 2:
            warnings.append(f"Q{q_no}: couldn't place both option rows; answer letters "
                            "may be off")

        def index(c):
            r = min(range(len(rows)), key=lambda k: abs(rows[k] - c["mid"])) if rows else 0
            col = 0 if abs(c["x0"] - cols[0]) <= abs(c["x0"] - cols[1]) else 1
            return r * 2 + col

        correct = next((index(c) for c in marks if c["kind"] == "correct_option"), None)
        wrong = next((index(c) for c in marks if c["kind"] == "your_wrong"), None)
        selected = correct if status == "correct" else wrong if status == "wrong" else None
        if correct is None:
            warnings.append(f"Q{q_no}: the correct option's green circle wasn't found")

        # Wording: question = lines above the first option row; each option =
        # words on its row, split into columns at the right-hand circle column.
        first_row = (rows[0] - 7) if rows else end
        q_words = []
        for l in block:
            if l["top"] < first_row:
                text = Q_RE.sub("", l["text"]) if l is label else l["text"]
                q_words.append(re.sub(r"\s*Marks\s*[:;]?\s*[-\dOo.]*\s*$", "", text).strip(" |"))
        options = [""] * 4
        for r, y in enumerate(rows):
            for col in (0, 1):
                words = [w["text"] for l in block for w in l.get("words", [])
                         if abs((w["top"] + w["bottom"]) / 2 - y) < 7
                         and (w["x0"] < cols[1] - 4 if col == 0 else w["x0"] >= cols[1] - 4)
                         and w["x0"] > label_x + 6]
                text = TAG_RE.sub("", " ".join(words)).strip(" |")
                options[r * 2 + col] = re.sub(r"^\W{0,2}[A-D©@®]?\W{0,2}\s+", "", text)

        questions.append(Question(
            q_no=q_no, section=sec["key"], section_name=sec["name"], part=sec["part"],
            question_text=" ".join(t for t in q_words if t), options=options,
            selected_index=selected, correct_index=correct, status=status))

    if total is not None:
        marking = config["marking"]
        ours = sum(marking.get(q.status, 0.0) for q in questions)
        if abs(ours - total) < 0.01:
            warnings.insert(0, f"Score matches Testbook's own total of {total:g}")
        else:
            warnings.insert(0, f"Score {ours:g} differs from Testbook's own total of {total:g}; "
                               "some questions may have been misread")
    return ParseResult(questions=questions, meta=SheetMeta(exam_level="Testbook mock test"),
                       warnings=warnings, source="image")
