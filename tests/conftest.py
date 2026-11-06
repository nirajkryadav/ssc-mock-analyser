import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"

# The API keeps analysed mocks; in tests that store lives in memory only.
os.environ.setdefault("RECORDS_STORE", "memory")


def _ensure_fixtures():
    if not (FIXTURES / "sample_response_sheet.pdf").exists():
        sys.path.insert(0, str(FIXTURES))
        import make_sample
        make_sample.build()


@pytest.fixture(scope="session")
def fixtures_dir():
    _ensure_fixtures()
    return FIXTURES


@pytest.fixture(scope="session")
def sample_pdf(fixtures_dir):
    return fixtures_dir / "sample_response_sheet.pdf"


@pytest.fixture(scope="session")
def part_pdfs(fixtures_dir):
    return sorted(fixtures_dir.glob("sample_part-*.pdf"))


@pytest.fixture(scope="session")
def scanned_pdf(fixtures_dir):
    """The same sheet flattened to page images, with no text layer."""
    return fixtures_dir / "sample_scanned.pdf"


@pytest.fixture(scope="session")
def ground_truth(fixtures_dir):
    data = json.loads((fixtures_dir / "sample_ground_truth.json").read_text())
    return {row["q_no"]: row for row in data}


@pytest.fixture(scope="session")
def config():
    import parser
    return parser.load_config()


@pytest.fixture(scope="session")
def parsed(sample_pdf, config):
    import parser
    import topics
    result = parser.parse_pdf(sample_pdf, config)
    topics.tag_questions(result.questions)
    return result
