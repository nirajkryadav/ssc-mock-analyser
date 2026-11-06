# SSC Mock Analyser

Upload your SSC CGL response sheet, get a scored breakdown, your weak topics,
and a seven-day study plan. Everything is computed in code, with no AI API and
no account. The web version saves each analysed mock so one page shows your
progress across all of them.

```
                   ┌─ text layer ─────────────┐
┌──────────────┐   │  pdfplumber words+rects  │   ┌────────┐  ┌─────────┐
│ response.pdf │ → ┤                          ├ → │ topics │→ │ scoring │ → report → app
└──────────────┘   │  pixels + OCR  ──────────│   └────────┘  └─────────┘
  in memory only   └─ no text layer ──────────┘
```

## What it does

- **Parses the sheet** with `pdfplumber`, reading the option-swatch fill colours
  that encode each answer: green (you picked it, right), red (you picked it,
  wrong), yellow (the right answer you missed), and the grey "Not Answered" bar.
- **Validates loudly**: 100 questions, one status each, 25 per section. A sheet
  that does not pass is reported rather than silently half-analysed.
- **Tags each question** with a topic from keyword dictionaries in `topics.yaml`.
- **Scores** it under a configurable marking scheme (+2 / −0.5 by default).
- **Finds weak areas**, ranked by marks lost, not by percentage.
- **Projects the gain from attempting more**, using your own accuracy rather than
  a 25% coin flip, and tells you the break-even accuracy for the marking scheme.
- **Tracks progress** across mocks. The web version saves every mock and shows
  score and section accuracy over time, the topics that keep costing marks, and
  a table of all past mocks you can reopen.
- **Writes a 7-day plan** from fixed templates in `plan_templates.yaml`.

## Getting your response sheet

1. Open your candidate response sheet in Chrome.
2. **Print** (Ctrl/Cmd + P) → **Destination: Save as PDF** → Save.
3. Repeat for each of PART-A to PART-D.

SSC serves one PDF per part, so you will normally have four files. Upload all
four at once — the app merges them and checks you have all 100 questions.

### If you only have a screen-capture PDF

Several "save page as PDF" browser extensions produce a file that is one bitmap
per page, with no text layer at all. The app reads those too, by a second path:
the option colours and the table rules are measured from the pixels, and the
words come from OCR. On macOS that is the built-in Vision framework and needs
no install; elsewhere install Tesseract (`apt install tesseract-ocr` plus
`pip install pytesseract`).

Measured on the test fixture flattened to images — 31 pages, no text layer:

| | Vision (macOS) | Tesseract (container) |
|---|---|---|
| Question statuses | 100 / 100 | 100 / 100 |
| Selected and correct option indices | 100 / 100 | 100 / 100 |
| Option text recovered exactly | 96 / 100 | 93 / 100 |
| Time | ~10 s | ~30 s |

Tesseract needs a denser render (300 dpi against Vision's 150) and sparse-text
mode. Left on its default page segmentation it tries to interpret the table
itself and splits option rows, which dropped option-index accuracy to 80/100;
`--psm 11` hands back words and lets this code do the layout, which fixes it.

Statuses are carried by colour rather than by text, which is why they survive
OCR intact. Only the question text degrades, and a garbled question can fall
through to an unclassified topic. The Print → Save as PDF route is still better
where you have the choice: it is instant and exact.

The one thing this path will not do is guess. Reading a sheet from colours
alone, with no text at all, was tried and dropped: the swatches say *what*
happened but not *which question* it happened to, and on the real sheet that
produced a confidently wrong score. If no OCR backend is available the app says
so rather than inventing numbers.

## Running it locally

Python 3.11 or newer.

```bash
git clone <your-repo-url>
cd "Anjali SSC"

python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

streamlit run app.py
```

The app opens at <http://localhost:8501>.

## Running the tests

```bash
pip install -r requirements-dev.txt
python tests/fixtures/make_sample.py    # builds the fixture PDFs
pytest
```

`make_sample.py` generates a 100-question response sheet in the real SSC layout
— the same colours, the repeated watermark number, PART headers, wrapped
options, skipped questions — together with the answer key it drew. It also
writes `sample_scanned.pdf`, the same sheet flattened to page images with the
text layer thrown away, which is what the image path is tested against. The
parser tests assert that parsing recovers the key exactly, question text and
all. Regenerating the fixture is deterministic, so the tests are reproducible.

The suite covers colour-to-status mapping (including colour-space variants and
re-encoding noise), block segmentation across page breaks, merging four part
files, the failure modes (a PDF with no questions, an image-only PDF with no
OCR available), the image path end to end, watermark rejection, question-number
repair, topic matching, scoring arithmetic, the guessing projection, history
merging, trends, plan generation and the Markdown report.

Tests that need OCR skip themselves where no backend exists, so the suite is
green on a bare Linux box as well as on macOS.

```
$ pytest
143 passed
```

## Configuration

Three YAML files, no code changes needed:

| File | Controls |
|---|---|
| `config.yaml` | marking scheme, section ranges, weak-area thresholds, the four status colours and their tolerance |

| `topics.yaml` | the keyword dictionaries, per section |
| `plan_templates.yaml` | the wording and shape of the 7-day plan |

**Improving the topic dictionaries.** Matching takes the first keyword hit in
YAML order, so put specific phrases above generic ones. Keywords match on word
boundaries and tolerate a plural, so `tan` will not fire inside "simultaneous"
and `simultaneous equation` still finds "simultaneous equations". Anything that
matches nothing lands in **Unclassified**, and the Topics tab offers an
`unmatched.csv` download listing exactly those questions — add the missing
keywords and re-run.

## Privacy

- The PDF is read from memory and discarded. It is never written to disk.
- The web version keeps the scored question records of each mock (in Firestore
  when deployed, in `.records/records.json` locally). There is no sign-in, so
  anyone with the site URL can see and delete them, including the name and roll
  number from the sheet.
- The Streamlit version saves the same records: to Firestore when a Firebase key
  is configured (the deployed app), otherwise to `.records/records.json`.

The one thing to know: on Streamlit Community Cloud the app runs on shared
infrastructure, and your sheet carries your name and roll number. If that
matters to you, run it locally — the instructions above are the whole setup.

## Two ways to run it

| | Streamlit (`app.py`) | Web (`web/` + `api/`) |
|---|---|---|
| Where | your machine, or Streamlit Community Cloud | Netlify + Cloud Run |
| Saved mocks | Firestore (free Spark plan) or a local file | Firestore via Cloud Run |
| OCR | macOS Vision locally, Tesseract on Streamlit Cloud | Tesseract, in the container |
| Cost | free | needs the Blaze plan |
| Setup | see the Streamlit Cloud section below | see [DEPLOY.md](DEPLOY.md) |

**The Streamlit version is the one in use.** The web version is kept as an
alternative. Both drive the same `parser` / `topics` / `scoring` / `report` /
`records` modules, so they always agree. Streamlit cannot be hosted on Netlify:
Netlify has no Python runtime, and Streamlit needs a persistent process with a
WebSocket per visitor.

### The web version, locally

```bash
.venv/bin/uvicorn api.main:app --reload --port 8080   # backend
cd web && python3 -m http.server 8888                 # frontend
```

`web/config.js` finds the local backend automatically.

Full deployment instructions, costs and troubleshooting: **[DEPLOY.md](DEPLOY.md)**.

## Deploying to Streamlit Community Cloud

This is the recommended way to use it from anywhere. It is free and needs no
card. Saved mocks go to Firestore, because Streamlit Cloud wipes the app's disk
whenever it restarts or goes to sleep.

**Firebase (done once).** The project `ssc-mock-analyser-nk` already exists,
with a Firestore database in `asia-south1` on the free Spark plan and security
rules (`firestore.rules`) that refuse every direct client. The app gets in with
a service-account key, which those rules do not apply to.

1. Open <https://console.firebase.google.com/project/ssc-mock-analyser-nk/settings/serviceaccounts/adminsdk>
   and click **Generate new private key**. A JSON file downloads. Treat it like
   a password: it can read and change the database.
2. To use it locally, either copy `.streamlit/secrets.toml.example` to
   `.streamlit/secrets.toml` and fill in the values from the JSON, or start the
   app with `FIREBASE_KEY_FILE=/path/to/key.json streamlit run app.py`. The
   caption under the title says which store is in use.

**Streamlit Cloud.**

1. Push this folder to a GitHub repository. Streamlit Community Cloud deploys
   only from GitHub. `secrets.toml`, the key JSON and `.records/` are
   git-ignored, so they stay off GitHub.
2. Go to <https://share.streamlit.io>, sign in with GitHub, and choose
   **Create app**. Pick the repository, set **Main file path** to `app.py`,
   and choose Python 3.12 under *Advanced settings*.
3. In *Advanced settings → Secrets*, paste the `[firebase]` section
   (see `.streamlit/secrets.toml.example`) with the values from the key JSON.
4. Deploy. The first build takes a few minutes. `packages.txt` installs
   Tesseract, so screen-capture PDFs work there too.

Notes:

- The app goes to sleep after a while with no visitors. The next visit shows a
  button to wake it, which takes about half a minute. Nothing saved is lost.
- There is no sign-in. Anyone with the app's URL can see the saved mocks,
  including the name and roll number printed on the sheet, and delete them.
- `.streamlit/config.toml` caps uploads at 25 MB per file and turns off usage
  stats.
- `reportlab` is in `requirements-dev.txt`, not `requirements.txt`. It only
  builds the test fixture, and keeping it out speeds up the deploy.

## Project layout

```
app.py                  Streamlit app: progress, analyse, all mocks, report
records.py              saved mocks: Firestore / JSON-file / memory stores
firestore.rules         closes the database to direct clients
packages.txt            apt packages for Streamlit Cloud (Tesseract)
api/main.py             HTTP API for the hosted version (FastAPI)
web/                    static front end deployed to Netlify
Dockerfile              backend container, Tesseract included
DEPLOY.md               Netlify + Cloud Run deployment
parser.py               PDF → question records, plus validation
image_source.py         the no-text-layer path: pixel colours, rules, OCR
ocr/vision_ocr.swift    offline OCR via the macOS Vision framework
topics.py               keyword → topic tagging, unmatched.csv
scoring.py              marks, accuracy, history merging and trends
report.py               weak areas, attempt advice, 7-day plan, Markdown
config.yaml             marking, sections, thresholds, colours
topics.yaml             keyword dictionaries
plan_templates.yaml     plan wording
tests/
  conftest.py           shared fixtures
  fixtures/make_sample.py   builds the sample PDF and its answer key
  test_parser.py        colour mapping, segmentation, validation
  test_topics.py        keyword matching and the unmatched log
  test_scoring.py       marks, accuracy, guessing projection
  test_report.py        weak areas, trends, plan, Markdown
  test_image_path.py    pixel detection, OCR path, numbering repair
  test_api.py           the HTTP endpoints, saved mocks, and failure modes
  test_app.py           Streamlit app: progress, saved mocks, open and delete
```

## Known limits

- **Images, fractions and math symbols** extract partially or not at all — on a
  real sheet this affected 9 of 100 questions, all of them figure-based or
  equation-heavy. The status colours are unaffected, so scoring stays correct;
  only the option text is short, and the parser records a warning naming each
  question. Topic tagging for those questions may fall through to Unclassified.
- **OCR is not exact.** On the image path, expect a few garbled characters in
  question text. Scores are not at risk, topic tagging mildly is.
- Topic tagging is a keyword lookup, not comprehension. It is right often enough
  to be useful and wrong often enough that `unmatched.csv` is worth reading.
- The Vision OCR helper is macOS-only and is compiled on first use (`swiftc`,
  from the Xcode command line tools). On other platforms the app uses Tesseract
  if it is installed.
