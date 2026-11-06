"""Read a response sheet that is page images rather than text.

Some candidates save their sheet with a screen-capture tool, which produces a
PDF holding one bitmap per page: no text, no vector rectangles, nothing a text
parser can use. This module rebuilds the same three inputs the vector path
gives the parser — words with positions, status-colour fills, and the table
rules — straight from the pixels.

Colours and rules come from the bitmap. Words come from OCR, which is optional:
without it the sheet still scores correctly, because the status of a question is
carried entirely by colour. Only the question text and topic tagging need OCR.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
VISION_SOURCE = HERE / "ocr" / "vision_ocr.swift"
VISION_BINARY = HERE / "ocr" / "vision_ocr"

DEFAULT_DPI = 150
#: Tesseract needs a denser render than Vision to read this sheet reliably.
BACKEND_DPI = {"vision": 150, "tesseract": 300}

# Sparse-text mode. We do our own line grouping and block segmentation from the
# word boxes, so Tesseract should find words and not impose a page layout of its
# own: its table handling splits and merges option rows. Measured on the test
# fixture, psm 11 lifts option-index accuracy from 80/100 to 100/100.
TESSERACT_CONFIG = "--psm 11 -c preserve_interword_spaces=1"


class OCRUnavailable(RuntimeError):
    """No OCR backend could be used on this machine."""


# --------------------------------------------------------------------- rendering


def render_pages(pdf, dpi: int = DEFAULT_DPI) -> list[Any]:
    """Rasterise every page of an open pdfplumber document."""
    images = []
    for page in pdf.pages:
        images.append(page.to_image(resolution=dpi).original.convert("RGB"))
    return images


def _scale(image, page_width: float) -> float:
    """Pixels per PDF point for this render."""
    return image.size[0] / page_width if page_width else 1.0


# ------------------------------------------------------------- colour detection


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _components(mask: np.ndarray, min_width: int, min_height: int) -> list[dict[str, int]]:
    """Bounding boxes of solid blocks in a boolean mask.

    The regions of interest are filled rectangles, so a row-run merge is enough
    and avoids pulling in scipy for a connected-component label.
    """
    height, width = mask.shape
    open_boxes: list[dict[str, int]] = []
    done: list[dict[str, int]] = []
    # One vectorised pass instead of a numpy call per pixel row: most rows of a
    # page hold no swatch at all, and this loop is the hot path of the OCR route.
    filled = mask.any(axis=1)

    for y in range(height):
        if not filled[y]:
            if open_boxes:
                done.extend(open_boxes)
                open_boxes = []
            continue
        row = mask[y]
        # Runs of True in this row.
        idx = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
        runs = [(int(a), int(b)) for a, b in zip(idx[::2], idx[1::2]) if b - a >= min_width]
        if not runs:
            done.extend(open_boxes)
            open_boxes = []
            continue
        still_open: list[dict[str, int]] = []
        for a, b in runs:
            for box in open_boxes:
                if box["bottom"] == y and box["x0"] < b and a < box["x1"]:
                    box["x0"] = min(box["x0"], a)
                    box["x1"] = max(box["x1"], b)
                    box["bottom"] = y + 1
                    still_open.append(box)
                    break
            else:
                still_open.append({"x0": a, "x1": b, "top": y, "bottom": y + 1})
        for box in open_boxes:
            if box not in still_open:
                done.append(box)
        open_boxes = still_open
    done.extend(open_boxes)
    return [b for b in done
            if b["x1"] - b["x0"] >= min_width and b["bottom"] - b["top"] >= min_height]


def detect_fills(image, colours: dict[str, Any], scale: float) -> list[dict[str, Any]]:
    """Find the status swatches and the "Not Answered" bar in a page bitmap."""
    arr = np.asarray(image, dtype=np.int16)
    width_px = arr.shape[1]
    out: list[dict[str, Any]] = []

    # The three option swatches are strongly saturated, so a loose tolerance is
    # safe. Grey is not, so it gets a tight one plus a width test: the only wide
    # grey block on the page is the "Not Answered" bar, everything else that
    # colour is a one-pixel table border.
    specs = [
        ("selected_correct", colours["selected_correct"], 70, 10, 10),
        ("selected_wrong", colours["selected_wrong"], 70, 10, 10),
        ("correct_option", colours["correct_option"], 70, 10, 10),
        ("not_answered", colours["not_answered"], 22, int(width_px * 0.35), 7),
    ]
    for status, hexval, tol, min_w, min_h in specs:
        target = np.array(_hex_to_rgb(hexval), dtype=np.int16)
        mask = np.abs(arr - target).max(axis=2) <= tol
        for box in _components(mask, min_w, min_h):
            out.append({
                "status": status,
                "x0": box["x0"] / scale,
                "x1": box["x1"] / scale,
                "top": box["top"] / scale,
                "bottom": box["bottom"] / scale,
                "mid": (box["top"] + box["bottom"]) / 2 / scale,
                "width": (box["x1"] - box["x0"]) / scale,
                "height": (box["bottom"] - box["top"]) / scale,
            })
    out.sort(key=lambda f: f["mid"])
    return _drop_legend(out)


def _drop_legend(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove the colour key printed in the sheet's header.

    The legend puts green, red and yellow side by side on one line. A real
    question never does: its swatches sit on separate option rows. So any band
    holding three different status colours is the legend, not an answer.
    """
    groups: list[list[dict[str, Any]]] = []
    for fill in fills:
        for group in groups:
            if any(abs(g["mid"] - fill["mid"]) <= max(g["height"], fill["height"])
                   for g in group):
                group.append(fill)
                break
        else:
            groups.append([fill])
    legend = {id(f) for g in groups if len({f["status"] for f in g}) >= 3 for f in g}
    return [f for f in fills if id(f) not in legend]


def detect_rules(image, scale: float, min_span: float = 0.35) -> list[float]:
    """Vertical positions of the table's horizontal borders.

    A border is a long unbroken run of non-white pixels. Text never produces
    one: a line of type is sparse, a rule is solid.
    """
    arr = np.asarray(image, dtype=np.int16)
    ink = arr.max(axis=2) < 235
    need = int(arr.shape[1] * min_span)

    tops: list[float] = []
    for y in range(ink.shape[0]):
        row = ink[y]
        if row.sum() < need:
            continue
        idx = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
        longest = max((int(b - a) for a, b in zip(idx[::2], idx[1::2])), default=0)
        if longest >= need:
            tops.append(y)

    merged: list[float] = []
    for t in tops:
        if not merged or t - merged[-1] > 2:
            merged.append(t)
    return [t / scale for t in merged]


# ------------------------------------------------------------------------- OCR


@dataclass
class OCRResult:
    words: list[dict[str, Any]]
    backend: str


def recommended_dpi(backend: str | None = None) -> int:
    """Render resolution to use for the backend that will actually run."""
    if backend is None:
        picks = available_backends()
        backend = picks[0] if picks else ""
    return BACKEND_DPI.get(backend, DEFAULT_DPI)


def available_backends() -> list[str]:
    backends = []
    if sys.platform == "darwin" and (VISION_BINARY.exists() or shutil.which("swift")):
        backends.append("vision")
    try:
        import pytesseract  # noqa: F401
        if shutil.which("tesseract"):
            backends.append("tesseract")
    except ImportError:
        pass
    return backends


def _ensure_vision_binary() -> Path | None:
    if VISION_BINARY.exists():
        return VISION_BINARY
    if not VISION_SOURCE.exists() or not shutil.which("swiftc"):
        return None
    try:
        subprocess.run(["swiftc", "-O", "-o", str(VISION_BINARY), str(VISION_SOURCE)],
                       check=True, capture_output=True, timeout=300)
    except (subprocess.SubprocessError, OSError):
        return None
    return VISION_BINARY if VISION_BINARY.exists() else None


def _ocr_vision(images, tmpdir: Path, scales: Sequence[float]) -> list[list[dict[str, Any]]]:
    """OCR through the macOS Vision framework — offline, no install."""
    paths = []
    for i, img in enumerate(images):
        p = tmpdir / f"page{i:03d}.png"
        img.save(p)
        paths.append(str(p))

    binary = _ensure_vision_binary()
    if binary is not None:
        cmd = [str(binary), *paths]
    elif shutil.which("swift"):
        cmd = ["swift", str(VISION_SOURCE), *paths]
    else:
        raise OCRUnavailable("The Vision helper could not be built or run.")

    proc = subprocess.run(cmd, capture_output=True, timeout=900)
    if proc.returncode != 0:
        raise OCRUnavailable(proc.stderr.decode("utf-8", "replace")[:400])
    pages = json.loads(proc.stdout.decode("utf-8"))

    out = []
    for page, scale in zip(pages, scales):
        out.append([{
            "text": w["text"],
            "x0": w["x0"] / scale, "x1": w["x1"] / scale,
            "top": w["top"] / scale, "bottom": w["bottom"] / scale,
        } for w in page["words"] if w["text"].strip()])
    return out


def workers(n_items: int, cap: int = 4) -> int:
    """How many pages to process at once: the CPUs this process may use, capped."""
    try:
        cpus = len(os.sched_getaffinity(0))  # respects container CPU pinning (Linux)
    except AttributeError:
        cpus = os.cpu_count() or 1
    return max(1, min(cap, cpus, n_items))


def _ocr_tesseract(images, scales: Sequence[float]) -> list[list[dict[str, Any]]]:
    n = workers(len(images))
    if n > 1:
        # Pages run as parallel tesseract processes; stop each one also spawning
        # its own OpenMP threads, which would fight over the same cores.
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")
        with ThreadPoolExecutor(n) as pool:
            return list(pool.map(lambda pair: _ocr_tesseract_page(*pair),
                                 zip(images, scales)))
    return [_ocr_tesseract_page(img, scale) for img, scale in zip(images, scales)]


def _ocr_tesseract_page(img, scale: float) -> list[dict[str, Any]]:
    import pytesseract
    from pytesseract import Output

    data = pytesseract.image_to_data(img, output_type=Output.DICT, config=TESSERACT_CONFIG)
    words = []
    for i, text in enumerate(data["text"]):
        # conf is -1 on the structural rows, whose text is blank anyway. A
        # higher floor was tried and discarded: it drops short real tokens
        # such as option letters and two-digit numbers.
        if not text.strip() or int(data["conf"][i]) < 0:
            continue
        x, y = data["left"][i], data["top"][i]
        w, h = data["width"][i], data["height"][i]
        words.append({"text": text, "x0": x / scale, "x1": (x + w) / scale,
                      "top": y / scale, "bottom": (y + h) / scale})
    return words


def ocr_pages(images, scales: Sequence[float], tmpdir: Path,
              backend: str | None = None) -> OCRResult:
    """Run OCR with the first backend that works, or raise OCRUnavailable."""
    order = [backend] if backend else available_backends()
    errors = []
    for name in order:
        try:
            if name == "vision":
                return OCRResult(_ocr_vision(images, tmpdir, scales), "vision")
            if name == "tesseract":
                return OCRResult(_ocr_tesseract(images, scales), "tesseract")
        except Exception as exc:  # a failed backend should fall through to the next
            errors.append(f"{name}: {exc}")
    raise OCRUnavailable(
        "No OCR backend is available. "
        + ("Tried — " + "; ".join(errors) if errors else
           "Install Tesseract (`brew install tesseract` / `apt install tesseract-ocr`) "
           "and `pip install pytesseract`, or run this on macOS where the built-in "
           "Vision framework is used.")
    )


# ------------------------------------------------------------------- watermarks


def drop_faint_words(image, words: Sequence[dict[str, Any]], scale: float,
                     ink_level: int = 130, min_ink: float = 0.02) -> list[dict[str, Any]]:
    """Remove words that are printed too faintly to be content.

    The sheet is watermarked with the candidate's roll number in pale grey,
    repeated across every page. OCR reads it, and often breaks it into short
    fragments that no "looks like a long number" rule would catch. Those
    fragments are not harmless: one surviving in a question block becomes a
    fifth option row, and the row-merging step then folds two real options
    together and shifts every option index after it.

    The pixels settle it. Printed text covers a few per cent of its box in dark
    ink; the watermark covers none. Testing the *proportion* of dark pixels
    rather than the darkest one matters, because OCR boxes are loose and a
    watermark box that clips a neighbouring rule or glyph would otherwise pass.
    """
    # int32: the weighted sum overflows int16 well before white.
    arr = np.asarray(image, dtype=np.int32)
    luma = (arr[:, :, 0] * 299 + arr[:, :, 1] * 587 + arr[:, :, 2] * 114) // 1000
    height, width = luma.shape

    kept = []
    for word in words:
        x0 = max(0, int(word["x0"] * scale))
        x1 = min(width, int(round(word["x1"] * scale)))
        y0 = max(0, int(word["top"] * scale))
        y1 = min(height, int(round(word["bottom"] * scale)))
        if x1 <= x0 or y1 <= y0:
            continue
        patch = luma[y0:y1, x0:x1]
        if patch.size and float((patch < ink_level).mean()) >= min_ink:
            kept.append(word)
    return kept
