"""Parse an SSC CGL response-sheet PDF into one record per question.

The sheet draws each question as a bordered block: a "Q.No: n" cell, the question
text, then four option rows. A small rect at the left edge of an option row carries
the status colour:

    green  the candidate selected this option and it was correct
    red    the candidate selected this option and it was wrong
    yellow this was the correct option and the candidate did not select it
    grey   a full-width "Not Answered" bar below the options

Everything else on the page -- blue buttons, the navbar, the repeated watermark
number -- is ignored.
"""
from __future__ import annotations

import io
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import pdfplumber
import yaml

CONFIG_PATH = Path(__file__).with_name("config.yaml")

IMAGE_ONLY_MESSAGE = (
    "This PDF contains only page images and nothing could be read from them. "
    "It was almost certainly produced by a screen-capture tool. Reopen the "
    "response sheet in Chrome and use Print → Destination: Save as PDF, which "
    "keeps the text and the option colours."
)

# OCR adds stray punctuation ("Q.No:; 51") and confuses o/0/a, so allow that.
# "Question No.: 51" on the challenge buttons does not match: after "Q" only
# a dot and spaces may come before "No".
Q_NO_RE = re.compile(r"Q\s*\.?\s*N[o0a]\s*[:.;,]{0,3}\s*(\d+)", re.I)
PART_RE = re.compile(r"\bPART\s*-\s*([A-D])\b", re.I)
NOT_ANSWERED_RE = re.compile(r"not\s*answered", re.I)
# Lines that are page chrome rather than question content.
CHROME_RE = re.compile(
    r"^(hindi version|english version|click here\b|save\s*/\s*print|view your responses|"
    r"ssc online examination|candidate response sheet|admin\s+logged\s*in|logout|"
    r"note:|undertaking by the candidate)",
    re.I,
)
STATUS_ORDER = ("correct", "wrong", "skipped")


class ParseError(Exception):
    """Raised when the PDF does not match the expected response-sheet layout."""


@dataclass
class Question:
    q_no: int
    section: str
    section_name: str
    part: str
    question_text: str
    options: list[str]
    selected_index: int | None
    correct_index: int | None
    status: str
    topic: str | None = None

    @property
    def selected_label(self) -> str:
        return _label(self.selected_index, self.options)

    @property
    def correct_label(self) -> str:
        return _label(self.correct_index, self.options)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SheetMeta:
    roll_no: str = ""
    candidate_name: str = ""
    exam_level: str = ""
    test_date: str = ""
    shift: str = ""
    centre: str = ""


@dataclass
class ParseResult:
    questions: list[Question]
    meta: SheetMeta
    warnings: list[str] = field(default_factory=list)
    #: "text" when the PDF had a text layer, "image" when it was read from pixels.
    source: str = "text"
    #: Which OCR backend supplied the words, if any.
    ocr_backend: str = ""


# --------------------------------------------------------------------------- config


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _label(index: int | None, options: Sequence[str]) -> str:
    if index is None:
        return "—"
    letter = "ABCD"[index] if 0 <= index < 4 else "?"
    text = options[index] if 0 <= index < len(options) else ""
    return f"{letter}. {text}" if text else letter


# --------------------------------------------------------------------------- colour


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def normalise_colour(raw: Any) -> tuple[int, int, int] | None:
    """Turn pdfplumber's non_stroking_color into 8-bit RGB.

    pdfplumber hands back whatever colour space the PDF used: a 3-tuple of RGB
    floats, a 1-tuple of grey, a 4-tuple of CMYK, a bare number, or occasionally
    values already in 0-255. All of them collapse to RGB here.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        raw = (raw,)
    try:
        vals = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    if not vals:
        return None

    scale = 255.0 if max(abs(v) for v in vals) <= 1.0 + 1e-6 else 1.0
    if len(vals) == 1:
        g = vals[0] * scale
        rgb = (g, g, g)
    elif len(vals) == 3:
        rgb = tuple(v * scale for v in vals)
    elif len(vals) == 4:
        cc, mm, yy, kk = vals if scale == 255.0 else [v / 255.0 for v in vals]
        rgb = (255 * (1 - min(1.0, cc + kk)),
               255 * (1 - min(1.0, mm + kk)),
               255 * (1 - min(1.0, yy + kk)))
    else:
        return None
    return tuple(max(0, min(255, int(round(v)))) for v in rgb)  # type: ignore[return-value]


def classify_colour(raw: Any, colours: dict[str, Any]) -> str | None:
    """Map a fill colour to a status name, or None if it is not a status colour."""
    rgb = normalise_colour(raw)
    if rgb is None:
        return None
    tol = float(colours.get("tolerance", 60))

    for ignore in colours.get("ignore", []) or []:
        if _within(rgb, _hex_to_rgb(ignore), tol):
            return None

    candidates = {
        "selected_correct": colours["selected_correct"],
        "selected_wrong": colours["selected_wrong"],
        "correct_option": colours["correct_option"],
        "not_answered": colours["not_answered"],
    }
    best, best_dist = None, None
    for name, hexval in candidates.items():
        target = _hex_to_rgb(hexval)
        dist = max(abs(a - b) for a, b in zip(rgb, target))
        if dist <= tol and (best_dist is None or dist < best_dist):
            best, best_dist = name, dist
    return best


def _within(rgb: tuple[int, int, int], target: tuple[int, int, int], tol: float) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(rgb, target))


# --------------------------------------------------------------------------- extraction


def _page_lines(page) -> list[dict[str, Any]]:
    """Group a pdfplumber page's words into lines, in reading order."""
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False,
                               extra_attrs=["size"]) or []
    return group_words(words)


def group_words(words: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group positioned words into lines with a vertical band each.

    Shared by the text and image paths: OCR hands back the same word shape that
    pdfplumber does, so everything downstream is identical.
    """
    lines: list[dict[str, Any]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        for line in reversed(lines):
            if abs(line["top"] - w["top"]) <= 3.0:
                line["words"].append(w)
                line["top"] = min(line["top"], w["top"])
                line["bottom"] = max(line["bottom"], w["bottom"])
                line["x0"] = min(line["x0"], w["x0"])
                line["x1"] = max(line["x1"], w["x1"])
                break
        else:
            lines.append({"top": w["top"], "bottom": w["bottom"], "x0": w["x0"],
                          "x1": w["x1"], "words": [w], "size": w.get("size", 0)})
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
        line["text"] = " ".join(w["text"] for w in line["words"]).strip()
        line["mid"] = (line["top"] + line["bottom"]) / 2
    lines.sort(key=lambda l: l["top"])
    return lines


def _watermark_text(lines: Iterable[dict[str, Any]]) -> set[str]:
    """Strings repeated many times on a page are the watermark, not content."""
    counts: dict[str, int] = {}
    for line in lines:
        for w in line["words"]:
            t = w["text"].strip()
            if len(t) >= 8 and t.isdigit():
                counts[t] = counts.get(t, 0) + 1
    return {t for t, n in counts.items() if n >= 4}


def _page_rules(page) -> list[float]:
    """Vertical positions of the table rules that separate rows.

    The sheet is an HTML table, so every option row is bounded by a horizontal
    border. Those borders are a far more reliable row separator than the gap
    between two lines of text, which collapses whenever an option wraps.
    """
    tops: list[float] = []
    min_width = page.width * 0.25
    for edge in page.edges or []:
        if edge.get("orientation") != "h":
            continue
        if abs(edge["x1"] - edge["x0"]) >= min_width:
            tops.append(float(edge["top"]))
    # Borders are often drawn twice (cell above and cell below); collapse them.
    tops.sort()
    merged: list[float] = []
    for t in tops:
        if not merged or t - merged[-1] > 1.5:
            merged.append(t)
    return merged


def _page_fills(page, colours: dict[str, Any]) -> list[dict[str, Any]]:
    """Every rect/curve on the page whose fill is one of the four status colours."""
    out = []
    for shape in list(page.rects) + list(page.curves):
        status = classify_colour(shape.get("non_stroking_color"), colours)
        if status is None:
            continue
        w = abs(shape["x1"] - shape["x0"])
        h = abs(shape["bottom"] - shape["top"])
        if w < 3 or h < 3:
            continue
        out.append({"status": status, "x0": shape["x0"], "x1": shape["x1"],
                    "top": shape["top"], "bottom": shape["bottom"],
                    "mid": (shape["top"] + shape["bottom"]) / 2,
                    "width": w, "height": h})
    return out


META_LABELS = [
    ("roll no", "roll_no"),
    ("candidate name", "candidate_name"),
    ("exam level", "exam_level"),
    ("test date", "test_date"),
    ("test time and shift", "shift"),
    ("centre name", "centre"),
]


def _parse_meta(lines: Sequence[dict[str, Any]]) -> SheetMeta:
    """Read the candidate details from the sheet header.

    Matching is on the label prefix rather than on the colon that separates
    label from value: OCR routinely loses a thin ":" glyph, and a header line
    that reads "Roll No. 226120418923" has to parse just as well as
    "Roll No. : 226120418923".
    """
    meta = SheetMeta()
    for line in lines[:60]:
        squashed = re.sub(r"\s+", " ", line["text"]).strip()
        bare = re.sub(r"[^a-z0-9 ]", " ", squashed.lower())
        bare = re.sub(r"\s+", " ", bare).strip()
        for label, attr in META_LABELS:
            if not bare.startswith(label) or getattr(meta, attr):
                continue
            # Walk the same number of words off the original, so the value keeps
            # its punctuation and case.
            value = " ".join(squashed.split()[len(label.split()):])
            value = value.lstrip(" :.-").strip()
            if value:
                setattr(meta, attr, value)
            break
    return meta


# --------------------------------------------------------------------------- blocks


def _collect_blocks(pages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Slice the document into one block per 'Q.No: n' anchor.

    A block runs from its anchor to the next question, to the next PART header,
    or over a page break — the sheet often starts a question near the foot of a
    page and finishes its options on the next one. Page coordinates are shifted
    onto one continuous axis so everything downstream can ignore pagination.
    """
    offset = 0.0
    offsets = []
    for page in pages:
        offsets.append(offset)
        offset += page["height"] + 1

    def shift(items, delta, key="top"):
        out = []
        for item in items:
            moved = dict(item)
            for field_name in ("top", "bottom", "mid"):
                if field_name in moved and moved[field_name] is not None:
                    moved[field_name] += delta
            out.append(moved)
        return out

    # Flatten the document onto one axis.
    lines: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    rules: list[float] = []
    for page, delta in zip(pages, offsets):
        lines.extend(shift(page["lines"], delta))
        fills.extend(shift(page["fills"], delta))
        rules.extend(r + delta for r in page["rules"])
    lines.sort(key=lambda l: l["top"])
    fills.sort(key=lambda f: f["mid"])
    rules.sort()

    anchors = []
    for line in lines:
        m = Q_NO_RE.search(line["text"])
        if m:
            anchors.append((int(m.group(1)), line))
    header_tops = [l["top"] for l in lines if PART_RE.search(l["text"])]

    blocks = []
    for i, (q_no, anchor) in enumerate(anchors):
        start = anchor["top"] - 1
        # The last question runs to the end of the document, not to the last line
        # of text: on a scan OCR can miss its option text, and stopping at the
        # last word read would cut off its answer colours.
        end = anchors[i + 1][1]["top"] if i + 1 < len(anchors) else max(
            offset, lines[-1]["bottom"] + 1)
        later = [t for t in header_tops if anchor["top"] < t < end]
        if later:
            end = min(later)
        blocks.append({
            "q_no": q_no,
            "page_starts": offsets[1:],
            "anchor_line": anchor,
            "lines": [l for l in lines if start <= l["top"] < end],
            "fills": [f for f in fills if start <= f["mid"] < end],
            "rules": [r for r in rules if start <= r < end],
        })
    return blocks


def _block_lines(block) -> list[dict[str, Any]]:
    return block["lines"]


def _block_fills(block) -> list[dict[str, Any]]:
    return block["fills"]


def _block_rules(block) -> list[float]:
    return block["rules"]


def repair_numbering(blocks: Sequence[dict[str, Any]]) -> list[str]:
    """Correct question numbers that OCR misread, using their position.

    Questions run in order down the sheet, so a block's number is predictable
    from its position. OCR confuses "Q.No: 100" with "Q.No: 10" and similar; the
    offset that most blocks agree on reveals which numbers are wrong. Only a
    handful of disagreements are repaired — if many blocks disagree the sheet is
    not what we think it is, and validation should say so rather than this
    quietly renumbering everything.
    """
    if not blocks:
        return []
    deltas = [b["q_no"] - i for i, b in enumerate(blocks)]
    offset = Counter(deltas).most_common(1)[0][0]
    wrong = [i for i, d in enumerate(deltas) if d != offset]
    if not wrong or len(wrong) > max(2, len(blocks) // 10):
        return []
    notes = []
    for i in wrong:
        was, now = blocks[i]["q_no"], i + offset
        blocks[i]["q_no"] = now
        notes.append(f"Question number read as {was} corrected to {now} from its "
                     f"position on the sheet")
    return notes


def _clean(text: str, watermarks: set[str]) -> str:
    tokens = [t for t in text.split() if t not in watermarks]
    return re.sub(r"\s+", " ", " ".join(tokens)).strip()


def _build_question(block, watermarks, section_lookup,
                    table_rows: bool = False) -> tuple[Question, list[str]]:
    warnings: list[str] = []
    q_no = block["q_no"]
    lines = _block_lines(block)
    fills = _block_fills(block)

    anchor = block["anchor_line"]
    # The question text sits to the right of the "Q.No: n" cell, on the anchor line
    # and any lines below it until the first option row starts.
    body = []
    for line in lines:
        text = _clean(line["text"], watermarks)
        if not text or CHROME_RE.match(text):
            continue
        if line is anchor:
            text = Q_NO_RE.sub("", text, count=1).lstrip(" :.").rstrip()
            if not text:
                continue
        if NOT_ANSWERED_RE.fullmatch(text.strip()):
            continue
        body.append({"text": text, "top": line["top"], "bottom": line["bottom"],
                     "mid": line["mid"], "x0": line["x0"]})

    status_fills = [f for f in fills if f["status"] != "not_answered"]
    not_answered = [f for f in fills if f["status"] == "not_answered"]

    rules = _block_rules(block)
    q_lines, opt_lines = _split_question_and_options(body, anchor, rules)
    options = _group_options(opt_lines, status_fills, rules)
    if len(options) != 4:
        warnings.append(f"Q{q_no}: found {len(options)} option rows, expected 4")
    if len(options) != 4 or table_rows:
        # Place options by the table's rows: a missing fragment otherwise shifts
        # the letter of your answer. (Not forced on every scan: OCR's own rows
        # are more reliable when it found all four.)
        options = _fill_missing_options(options, anchor, rules,
                                        block.get("page_starts", ()), force=table_rows)

    # Status comes from which colours are present, not from matching them to a
    # row. Questions whose options are images extract no option text at all, and
    # those must still score correctly.
    kinds = {f["status"] for f in status_fills}
    if "selected_correct" in kinds:
        status = "correct"
    elif "selected_wrong" in kinds:
        status = "wrong"
    elif not_answered or "correct_option" in kinds:
        status = "skipped"
    else:
        status = "skipped"
        warnings.append(f"Q{q_no}: no status colour found")

    selected_index = correct_index = None
    for fill in status_fills:
        idx = _option_for(fill, options)
        if idx is None:
            continue
        if fill["status"] == "selected_correct":
            selected_index = correct_index = idx
        elif fill["status"] == "selected_wrong":
            selected_index = idx
        elif fill["status"] == "correct_option":
            correct_index = idx

    if status == "skipped":
        selected_index = None
    elif status == "correct":
        correct_index = selected_index

    section = section_lookup(q_no)
    question = Question(
        q_no=q_no,
        section=section["key"],
        section_name=section["name"],
        part=section["part"],
        question_text=" ".join(l["text"] for l in q_lines).strip(),
        options=[o["text"] for o in options],
        selected_index=selected_index,
        correct_index=correct_index,
        status=status,
    )
    return question, warnings


def _band_index(top: float, rules: Sequence[float]) -> int:
    """How many table rules sit above this y position."""
    return sum(1 for r in rules if r <= top + 0.5)


def _split_question_and_options(body, anchor, rules):
    """Separate the question cell from the four option rows.

    The sheet is a table, so the question lives in the row holding the "Q.No"
    cell and the options are the rows beneath it. Without rules (a sheet drawn
    without borders) the last four text groups are taken as the options.
    """
    if not body:
        return [], []
    if rules:
        q_band = _band_index(anchor["top"], rules)
        q_lines = [l for l in body if _band_index(l["top"], rules) <= q_band]
        opt_lines = [l for l in body if _band_index(l["top"], rules) > q_band]
        if opt_lines:
            return q_lines, opt_lines
    split = max(1, len(body) - 4)
    return body[:split], body[split:]


def _group_options(opt_lines, status_fills, rules) -> list[dict[str, Any]]:
    """Fold wrapped lines into (at most four) option rows."""
    if not opt_lines:
        return []
    groups: list[dict[str, Any]] = []
    if rules:
        last_band = None
        for line in opt_lines:
            band = _band_index(line["top"], rules)
            if band == last_band and groups:
                groups[-1]["text"] += " " + line["text"]
                groups[-1]["bottom"] = line["bottom"]
            else:
                groups.append({"text": line["text"], "top": line["top"],
                               "bottom": line["bottom"]})
                last_band = band
    else:
        gaps = [b["top"] - a["bottom"] for a, b in zip(opt_lines, opt_lines[1:])]
        typical = min((g for g in gaps if g >= 0), default=0.0)
        for line in opt_lines:
            if groups and _is_continuation(line, groups[-1], status_fills, typical):
                groups[-1]["text"] += " " + line["text"]
                groups[-1]["bottom"] = line["bottom"]
            else:
                groups.append({"text": line["text"], "top": line["top"],
                               "bottom": line["bottom"]})
    # A wrapped option can still over-split; merge the closest pairs down to four.
    while len(groups) > 4:
        gaps = [(groups[i + 1]["top"] - groups[i]["bottom"], i) for i in range(len(groups) - 1)]
        _, i = min(gaps)
        groups[i]["text"] += " " + groups[i + 1]["text"]
        groups[i]["bottom"] = groups[i + 1]["bottom"]
        del groups[i + 1]
    for g in groups:
        g["mid"] = (g["top"] + g["bottom"]) / 2
    return groups


def _rule_clusters(rules: Sequence[float], gap: float = 3.0) -> list[list[float]]:
    """Group rules that sit within `gap` of each other: on a scan one border can be
    detected as several rules a pixel apart, and a thick border as a run of them."""
    out: list[list[float]] = []
    for r in sorted(rules):
        if out and r - out[-1][-1] <= gap:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def _option_rows(anchor_top: float, rules: Sequence[float],
                 page_starts: Sequence[float] = ()) -> list[tuple[float, float]] | None:
    """The four option rows of a question, from the table's lines alone.

    Each question is a table: the question cell (which may hold a figure with
    lines of its own), then four option rows, then a thick bottom border. So
    the four rows directly above the first thick border are A to D; without
    one, the four rows after the question cell. A band that straddles a page
    join is the white margin between two screenshots, not a row: the picture
    edges there look like table lines.
    """
    clusters = [c for c in _rule_clusters(rules) if c[0] > anchor_top]
    bands = [(clusters[i][0], clusters[i + 1][0], len(clusters[i + 1]) >= 3)
             for i in range(len(clusters) - 1)]
    bands = [b for b in bands if not any(b[0] < s < b[1] for s in page_starts)]
    # A figure inside the question cell can have a thick frame of its own, so
    # only a thick border with room for four rows above it can end the options.
    ends = [i for i, b in enumerate(bands) if b[2] and i >= 3]
    if ends:
        rows = [(t, b) for t, b, _ in bands[ends[0] - 3: ends[0] + 1]]
    elif len(bands) >= 4:
        rows = [(t, b) for t, b, _ in bands[:4]]
    else:
        return None
    if any(not 8 <= bottom - top <= 120 for top, bottom in rows):
        return None  # not option-sized rows; the table isn't what we think
    return rows


def _fill_missing_options(options, anchor, rules, page_starts=(),
                          force: bool = False) -> list[dict[str, Any]]:
    """Rebuild the four option rows from the table when OCR missed some.

    A missed row (OCR drops a short option such as "32", or the options are
    pictures with no text at all) would otherwise shift the options below it,
    and a colour in row B would be credited to row A. Rows come from the table
    lines; whatever text OCR did read is put in the row it sits in, and a row
    with none is left empty.
    """
    if not rules or (len(options) >= 4 and not force):
        return options
    rows = _option_rows(anchor["top"], rules, page_starts)
    if rows is None:
        return options
    slots = []
    for top, bottom in rows:
        inside = [o for o in options if top - 1 <= o["mid"] <= bottom + 1]
        slots.append({"text": " ".join(o["text"] for o in inside), "top": top,
                      "bottom": bottom, "mid": (top + bottom) / 2})
    return slots


def _is_continuation(line, prev, status_fills, typical_gap) -> bool:
    """Spacing fallback used only when the block has no table rules."""
    for fill in status_fills:
        if prev["bottom"] < fill["top"] <= line["top"] + 1:
            return False
    gap = line["top"] - prev["bottom"]
    return gap <= max(2.0, typical_gap * 1.8)


def _option_for(fill, options) -> int | None:
    """Which option row does this swatch belong to?"""
    if not options:
        return None
    inside = [i for i, o in enumerate(options)
              if fill["top"] - 2 <= o["mid"] <= fill["bottom"] + 2]
    if inside:
        return inside[0]
    return min(range(len(options)), key=lambda i: abs(options[i]["mid"] - fill["mid"]))


# --------------------------------------------------------------------------- public API


def _pages_from_text(pdf, colours) -> list[dict[str, Any]]:
    pages = []
    for page in pdf.pages:
        lines = _page_lines(page)
        pages.append({"lines": lines, "fills": _page_fills(page, colours),
                      "rules": _page_rules(page), "height": page.height,
                      "watermarks": _watermark_text(lines)})
    return pages


def _pages_from_images(pdf, colours, dpi: int | None) -> tuple[list[dict[str, Any]], str]:
    """Rebuild the parser's inputs from page bitmaps.

    Colours and table rules come from the pixels; the words come from OCR.
    OCR is required rather than optional: a swatch colour alone cannot say which
    question the swatch belongs to once several share a page, so a sheet read
    without text would produce a confident but wrong score.
    """
    import image_source

    if dpi is None:
        dpi = image_source.recommended_dpi()
    images = image_source.render_pages(pdf, dpi)
    widths = [page.width for page in pdf.pages]
    heights = [page.height for page in pdf.pages]
    scales = [img.size[0] / w if w else 1.0 for img, w in zip(images, widths)]

    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = image_source.ocr_pages(images, scales, Path(tmp))
        except image_source.OCRUnavailable as exc:
            raise ParseError(
                "This PDF contains only page images, so it has to be read with OCR, "
                f"and no OCR backend is available.\n\n{exc}\n\nThe simpler fix is to "
                "reopen the response sheet in Chrome and use Print \u2192 Destination: "
                "Save as PDF, which keeps the text and needs no OCR."
            ) from exc
    words_per_page, backend = result.words, result.backend

    # Work out the watermark from the *raw* OCR, before the faint-ink filter
    # thins it out. A copy printed over the grey "Not Answered" bar is dark
    # enough to survive that filter, and by then too few copies remain for a
    # "repeated across the page" rule to recognise it.
    repeated = _repeated_numbers(w["text"] for page in words_per_page for w in page)

    def one_page(img, scale, height, words):
        words = image_source.drop_faint_words(img, words, scale)
        lines = group_words(words)
        return {
            "lines": lines,
            "fills": image_source.detect_fills(img, colours, scale),
            "rules": image_source.detect_rules(img, scale),
            "height": height,
            "watermarks": _watermark_text(lines) | repeated,
        }

    # Pixel work is mostly numpy, which releases the GIL, so pages overlap well.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(image_source.workers(len(images))) as pool:
        pages = list(pool.map(one_page, images, scales, heights, words_per_page))
    # Kept so parse_pdf can re-read pages where question labels went missing.
    state = {"images": images, "scales": scales, "heights": heights,
             "words": words_per_page, "one_page": one_page}
    return pages, backend, state


def _anchor_pages(blocks, pages) -> dict[int, int]:
    """Which page each question's "Q.No" label is on (blocks use one long axis)."""
    starts, offset = [], 0.0
    for page in pages:
        starts.append(offset)
        offset += page["height"] + 1
    out = {}
    for b in blocks:
        top = b["anchor_line"]["top"]
        out[b["q_no"]] = max(i for i, s in enumerate(starts) if s <= top)
    return out


def _gap_pages(blocks, pages, sections) -> tuple[list[int], set[int]]:
    """Question numbers missing from this file, and the pages that should hold them.

    The file's expected range is the PART(s) its questions belong to, so a gap
    at the start or end of a PART counts as well as one in the middle.
    """
    if not blocks:
        return [], set()
    where = _anchor_pages(blocks, pages)
    found = sorted(where)
    lo = next((s["first_q"] for s in sections if s["first_q"] <= found[0] <= s["last_q"]),
              found[0])
    hi = next((s["last_q"] for s in sections if s["first_q"] <= found[-1] <= s["last_q"]),
              found[-1])
    missing = [n for n in range(lo, hi + 1) if n not in where]
    todo: set[int] = set()
    for n in missing:
        before = [q for q in found if q < n]
        after = [q for q in found if q > n]
        first = where[before[-1]] if before else 0
        last = where[after[0]] if after else len(pages) - 1
        todo.update(range(first, last + 1))
    return missing, todo


def _rescue_missing(pages, state, blocks, sections) -> tuple[list[dict[str, Any]], list[str]]:
    """Re-read the pages around missing question numbers with cleaner images.

    Tesseract reading a whole page sometimes skips a "Q.No: n" label that it
    reads perfectly on a cleaner image; the question then vanishes from the
    score. The faint roll-number watermark is the usual trigger. Here the pages
    that should hold a missing number are OCR'd again, thresholded so the
    watermark disappears, and any word the first pass did not find is added.
    Nothing the first pass read is replaced.
    """
    import image_source

    missing, todo = _gap_pages(blocks, pages, sections)
    if not missing or "tesseract" not in image_source.available_backends():
        return pages, []
    pages = list(pages)
    reread: set[int] = set()
    for variant in image_source.RESCUE_VARIANTS:
        if not todo:
            break
        with ThreadPoolExecutor(image_source.workers(len(todo))) as pool:
            extra = dict(zip(sorted(todo), pool.map(
                lambda i: image_source.ocr_variant(state["images"][i], state["scales"][i],
                                                   variant), sorted(todo))))
        reread.update(extra)
        for i, words in extra.items():
            state["words"][i] = image_source.merge_words(state["words"][i], words)
            pages[i] = state["one_page"](state["images"][i], state["scales"][i],
                                         state["heights"][i], state["words"][i])
        blocks = _collect_blocks(pages)
        repair_numbering(blocks)
        still, todo = _gap_pages(blocks, pages, sections)
        if not still:
            break
    recovered = sorted(set(missing) - set(_gap_pages(blocks, pages, sections)[0]))
    notes = [f"Re-read {len(reread)} page(s) to recover question(s) "
             f"{', '.join(map(str, recovered))} that the first OCR pass missed"] if recovered else []
    return pages, notes


def _repeated_numbers(tokens: Iterable[str], min_length: int = 6,
                      min_count: int = 4) -> set[str]:
    """Long numeric strings that recur across the document: the watermark."""
    counts = Counter(t.strip() for t in tokens
                     if len(t.strip()) >= min_length and t.strip().isdigit())
    return {t for t, n in counts.items() if n >= min_count}


#: Below this many pixels across, OCR misreads this sheet. Measured on the test
#: sheet: 1224 and 1275 px read 100/100; 918 px misread several questions.
LOW_RES_PX = 1100


def _picture_width(pdf) -> int | None:
    """Pixel width of the narrowest full-page picture in an image-only PDF.

    Pixels, not dpi: screenshot tools often size the PDF page so the picture is
    "72 dpi" however sharp it really is, so dpi alone says little.
    """
    widths = []
    for page in pdf.pages:
        for im in page.images:
            src = im.get("srcsize") or (0, 0)
            if float(im["x1"]) - float(im["x0"]) > page.width * 0.5 and src[0]:
                widths.append(int(src[0]))
    return min(widths) if widths else None


def parse_pdf(source: str | Path | bytes | io.BytesIO,
              config: dict[str, Any] | None = None,
              allow_image_fallback: bool = True,
              dpi: int | None = None) -> ParseResult:
    """Parse one response-sheet PDF. `source` may be a path or in-memory bytes.

    A PDF with no text layer — the kind a screen-capture tool produces — is read
    from its pixels instead, unless `allow_image_fallback` is off. `dpi` defaults
    to whatever suits the OCR backend that will be used.
    """
    cfg = config or load_config()
    colours = cfg["colours"]
    sections = cfg["sections"]

    def section_lookup(q_no: int) -> dict[str, Any]:
        for sec in sections:
            if sec["first_q"] <= q_no <= sec["last_q"]:
                return sec
        return {"key": "unknown", "name": "Unknown", "part": "?"}

    handle = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    warnings: list[str] = []
    kind, backend = "text", ""

    with pdfplumber.open(handle) as pdf:
        if not pdf.pages:
            raise ParseError("The PDF has no pages.")

        pages = _pages_from_text(pdf, colours)
        image_only = all(not page["lines"] for page in pages)

        if image_only:
            if not allow_image_fallback:
                raise ParseError(IMAGE_ONLY_MESSAGE)
            kind = "image"
            pages, backend, ocr_state = _pages_from_images(pdf, colours, dpi)
            width_px = _picture_width(pdf)
            if width_px and width_px < LOW_RES_PX:
                warnings.append(
                    f"Low-resolution scan: the page pictures are about {width_px} pixels "
                    "wide, so some question or option text may be misread, and on very "
                    "small pictures even an answer. A sharper capture, or Print \u2192 "
                    "Save as PDF from Chrome, is more reliable.")

    meta = SheetMeta()
    for page in pages:
        found = _parse_meta(page["lines"])
        if found.roll_no or found.candidate_name:
            meta = found
            break

    watermarks: set[str] = set()
    for page in pages:
        watermarks |= page["watermarks"]

    blocks = _collect_blocks(pages)
    repairs = repair_numbering(blocks)
    if kind == "image" and blocks:
        pages, notes = _rescue_missing(pages, ocr_state, blocks, sections)
        if notes:
            warnings.extend(notes)
            blocks = _collect_blocks(pages)
            repairs = repair_numbering(blocks)
    warnings.extend(repairs)
    if not blocks:
        raise ParseError(
            IMAGE_ONLY_MESSAGE if kind == "image" else
            "No 'Q.No:' markers were found. This does not look like an SSC CGL "
            "candidate response sheet."
        )

    questions: list[Question] = []
    for block in blocks:
        question, warns = _build_question(block, watermarks, section_lookup)
        questions.append(question)
        warnings.extend(warns)

    questions.sort(key=lambda q: q.q_no)
    return ParseResult(questions=questions, meta=meta, warnings=warnings,
                       source=kind, ocr_backend=backend)


def parse_many(sources: Sequence[Any], config: dict[str, Any] | None = None,
               **kwargs) -> ParseResult:
    """Parse one combined sheet, or several part files, into a single result.

    SSC serves one PDF per PART, so a full mock usually arrives as four uploads.
    Later files win on duplicate question numbers.
    """
    cfg = config or load_config()
    merged: dict[int, Question] = {}
    meta = SheetMeta()
    warnings: list[str] = []
    kind, backend = "text", ""
    for src in sources:
        result = parse_pdf(src, cfg, **kwargs)
        for q in result.questions:
            merged[q.q_no] = q
        warnings.extend(result.warnings)
        if result.source == "image":
            kind = "image"
        backend = backend or result.ocr_backend
        if not meta.roll_no and result.meta.roll_no:
            meta = result.meta

    questions = [merged[k] for k in sorted(merged)]
    return ParseResult(questions=questions, meta=meta,
                       warnings=sorted(set(warnings), key=warnings.index),
                       source=kind, ocr_backend=backend)


# --------------------------------------------------------------------------- validation


def validate(result: ParseResult, config: dict[str, Any] | None = None,
             strict: bool = True) -> list[str]:
    """Check the parse against the known shape of a CGL Tier-1 sheet.

    Returns the list of problems. With strict=True a non-empty list raises.
    """
    cfg = config or load_config()
    sections = cfg["sections"]
    expected_total = sections[-1]["last_q"]
    problems: list[str] = []
    questions = result.questions

    if len(questions) != expected_total:
        problems.append(f"expected {expected_total} questions, found {len(questions)}")

    numbers = [q.q_no for q in questions]
    missing = sorted(set(range(1, expected_total + 1)) - set(numbers))
    if missing:
        problems.append(f"missing question numbers: {_compact(missing)}")
    dupes = sorted({n for n in numbers if numbers.count(n) > 1})
    if dupes:
        problems.append(f"duplicate question numbers: {_compact(dupes)}")

    for sec in sections:
        want = sec["last_q"] - sec["first_q"] + 1
        got = sum(1 for q in questions if q.section == sec["key"])
        if got != want:
            problems.append(f"{sec['part']} ({sec['name']}): expected {want} questions, found {got}")

    for q in questions:
        if q.status not in STATUS_ORDER:
            problems.append(f"Q{q.q_no}: unknown status {q.status!r}")
        if q.status == "skipped" and q.selected_index is not None:
            problems.append(f"Q{q.q_no}: marked skipped but an option was selected")
        # Index checks only apply where option text was recovered. A question
        # whose options are images has a reliable status but no row to point at.
        if len(q.options) == 4:
            if q.status == "correct" and q.selected_index is None:
                problems.append(f"Q{q.q_no}: marked correct but no option was identified")
            if q.status == "wrong" and q.selected_index is None:
                problems.append(f"Q{q.q_no}: marked wrong but no selected option")
            if q.status == "wrong" and q.correct_index is None:
                problems.append(f"Q{q.q_no}: marked wrong but the correct option was not found")
            if q.status == "wrong" and q.selected_index == q.correct_index:
                problems.append(f"Q{q.q_no}: marked wrong but selected == correct")
            if q.status == "skipped" and q.correct_index is None:
                problems.append(f"Q{q.q_no}: skipped but no yellow correct option was found")

    if strict and problems:
        raise ParseError("Validation failed:\n  - " + "\n  - ".join(problems))
    return problems


def _compact(numbers: Sequence[int]) -> str:
    if len(numbers) <= 12:
        return ", ".join(str(n) for n in numbers)
    return ", ".join(str(n) for n in numbers[:12]) + f", … (+{len(numbers) - 12} more)"


def validation_report(result: ParseResult, config: dict[str, Any] | None = None) -> str:
    """Human-readable summary of what was parsed and whether it hangs together."""
    cfg = config or load_config()
    problems = validate(result, cfg, strict=False)
    q = result.questions
    counts = {s: sum(1 for x in q if x.status == s) for s in STATUS_ORDER}
    lines = [
        "SSC response sheet — validation report",
        "=" * 56,
        f"Candidate   : {result.meta.candidate_name or '(not found)'}",
        f"Roll No.    : {result.meta.roll_no or '(not found)'}",
        f"Test date   : {result.meta.test_date or '(not found)'}   {result.meta.shift}",
        f"Questions   : {len(q)}",
        f"Status      : {counts['correct']} correct, {counts['wrong']} wrong, "
        f"{counts['skipped']} skipped",
        "",
        "Section counts",
    ]
    for sec in cfg["sections"]:
        got = sum(1 for x in q if x.section == sec["key"])
        want = sec["last_q"] - sec["first_q"] + 1
        flag = "ok" if got == want else "MISMATCH"
        lines.append(f"  {sec['part']}  {sec['name']:<36} {got:>3} / {want:<3} {flag}")
    lines.append("")
    if result.warnings:
        lines.append(f"Warnings ({len(result.warnings)}):")
        lines.extend(f"  - {w}" for w in result.warnings[:15])
        if len(result.warnings) > 15:
            lines.append(f"  … (+{len(result.warnings) - 15} more)")
        lines.append("")
    if problems:
        lines.append(f"FAILED — {len(problems)} problem(s):")
        lines.extend(f"  - {p}" for p in problems)
    else:
        lines.append("PASSED — 100 questions, one status each, 25/25/25/25 by section.")
    return "\n".join(lines)
