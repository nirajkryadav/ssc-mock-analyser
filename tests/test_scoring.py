"""Scoring: marks, accuracy, attempt rate and the guessing projection."""
import pytest

import scoring
from parser import Question

MARKING = {"correct": 2.0, "wrong": -0.5, "skipped": 0.0}

CONFIG = {
    "marking": MARKING,
    "sections": [
        {"key": "reasoning", "name": "Reasoning", "part": "PART-A", "first_q": 1, "last_q": 2},
        {"key": "quant", "name": "Quant", "part": "PART-C", "first_q": 3, "last_q": 4},
    ],
}


def q(q_no, section, status, topic):
    return Question(q_no=q_no, section=section, section_name=section.title(), part="PART-X",
                    question_text="t", options=["a", "b", "c", "d"],
                    selected_index=0 if status != "skipped" else None,
                    correct_index=0 if status == "correct" else 1,
                    status=status, topic=topic)


@pytest.fixture
def sheet():
    return [
        q(1, "reasoning", "correct", "Series"),
        q(2, "reasoning", "wrong", "Series"),
        q(3, "quant", "skipped", "Ratio"),
        q(4, "quant", "correct", "Ratio"),
    ]


def test_total_score_applies_the_marking_scheme(sheet):
    card = scoring.score(sheet, CONFIG)
    # 2 correct (+4), 1 wrong (-0.5), 1 skipped (0)
    assert card.overall.score == pytest.approx(3.5)
    assert card.overall.max_score == pytest.approx(8.0)


def test_counts_add_up(sheet):
    o = scoring.score(sheet, CONFIG).overall
    assert (o.total, o.correct, o.wrong, o.skipped) == (4, 2, 1, 1)
    assert o.attempted == 3


def test_accuracy_is_over_attempted_not_total(sheet):
    o = scoring.score(sheet, CONFIG).overall
    assert o.accuracy == pytest.approx(2 / 3)
    assert o.attempt_rate == pytest.approx(3 / 4)
    assert o.skip_rate == pytest.approx(1 / 4)


def test_accuracy_of_an_untouched_paper_is_zero_not_an_error():
    card = scoring.score([q(1, "quant", "skipped", "Ratio")], CONFIG)
    assert card.overall.accuracy == 0.0
    assert card.overall.attempt_rate == 0.0


def test_section_breakdown(sheet):
    card = scoring.score(sheet, CONFIG)
    by_name = {b.label: b for b in card.sections}
    assert by_name["Reasoning"].score == pytest.approx(1.5)
    assert by_name["Quant"].score == pytest.approx(2.0)
    assert by_name["Quant"].skipped == 1


def test_topic_breakdown_is_sorted_by_marks_lost(sheet):
    card = scoring.score(sheet, CONFIG)
    # Series loses 2.5 marks (one wrong), Ratio loses 2.0 (one skipped).
    assert [b.label for b in card.topics] == ["Series", "Ratio"]
    assert card.topic_sections["Ratio"] == "Quant"


def test_marks_lost_is_against_a_perfect_slice(sheet):
    card = scoring.score(sheet, CONFIG)
    ratio = next(b for b in card.topics if b.label == "Ratio")
    assert ratio.marks_lost == pytest.approx(4.0 - 2.0)


def test_marking_scheme_is_configurable(sheet):
    cfg = {**CONFIG, "marking": {"correct": 1.0, "wrong": -0.25, "skipped": 0.0}}
    card = scoring.score(sheet, cfg)
    assert card.overall.score == pytest.approx(2 * 1.0 - 0.25)


def test_guessing_projection_uses_the_candidates_own_accuracy(sheet):
    card = scoring.score(sheet, CONFIG)
    gain = scoring.expected_gain_from_guessing(card)
    acc = 2 / 3
    assert gain["accuracy_used"] == pytest.approx(acc)
    assert gain["per_question"] == pytest.approx(acc * 2.0 + (1 - acc) * -0.5)
    assert gain["expected_total"] == pytest.approx(gain["per_question"] * 1)


def test_breakeven_accuracy_for_the_default_scheme():
    card = scoring.score([q(1, "quant", "correct", "Ratio")], CONFIG)
    # +2 / -0.5 means you break even at 1 in 5.
    assert scoring.expected_gain_from_guessing(card)["breakeven_accuracy"] == pytest.approx(0.2)


def test_guessing_can_be_negative_for_a_weak_candidate():
    sheet = [q(1, "quant", "wrong", "Ratio"), q(2, "quant", "wrong", "Ratio"),
             q(3, "quant", "correct", "Ratio"), q(4, "quant", "skipped", "Ratio")]
    card = scoring.score(sheet, CONFIG)
    gain = scoring.expected_gain_from_guessing(card)
    assert gain["accuracy_used"] == pytest.approx(1 / 3)
    assert gain["expected_total"] > 0  # 33% clears the 20% break-even
    worse = scoring.expected_gain_from_guessing(card, accuracy=0.10)
    assert worse["expected_total"] < 0


def test_questions_frame_has_one_row_per_question(sheet):
    df = scoring.questions_frame(sheet, "2026-10-28", "Mock 1")
    assert len(df) == 4
    assert list(df.columns) == ["mock_date", "mock_name", "q_no", "section", "topic",
                                "status", "selected_option", "correct_option"]
    assert df["mock_name"].unique().tolist() == ["Mock 1"]


def test_selected_option_is_blank_for_a_skip(sheet):
    df = scoring.questions_frame(sheet, "2026-10-28")
    row = df[df["q_no"] == 3].iloc[0]
    assert row["status"] == "skipped"
    assert row["selected_option"] == "—"


def test_real_sheet_scores_consistently(parsed, config):
    card = scoring.score(parsed.questions, config)
    assert card.overall.total == 100
    assert sum(b.total for b in card.sections) == 100
    assert card.overall.score == pytest.approx(
        card.overall.correct * 2.0 + card.overall.wrong * -0.5)
