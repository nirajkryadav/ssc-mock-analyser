"""Report rules: weak areas, attempt advice, trends, plan and Markdown output."""
import pandas as pd
import pytest

import report as report_mod
import scoring
from parser import Question, SheetMeta

CONFIG = {
    "marking": {"correct": 2.0, "wrong": -0.5, "skipped": 0.0},
    "sections": [
        {"key": "quant", "name": "Quantitative Aptitude", "part": "PART-C",
         "first_q": 1, "last_q": 6},
        {"key": "english", "name": "English Comprehension", "part": "PART-D",
         "first_q": 7, "last_q": 10},
    ],
    "analysis": {
        "weak_topic_accuracy": 0.60,
        "weak_topic_skip_rate": 0.50,
        "weak_topic_min_questions": 2,
        "target_attempt_rate": 0.80,
    },
}

TEMPLATES = report_mod.load_templates()


def q(q_no, section, section_name, status, topic):
    return Question(q_no=q_no, section=section, section_name=section_name, part="PART-X",
                    question_text=f"Question {q_no}", options=["a", "b", "c", "d"],
                    selected_index=None if status == "skipped" else 0,
                    correct_index=0 if status == "correct" else 1,
                    status=status, topic=topic)


@pytest.fixture
def sheet():
    #  Ratio: 1 right, 3 wrong  -> 25% accuracy, weak
    #  Mensuration: 2 skipped   -> 100% skipped, weak
    #  Synonym: 4 right         -> strong
    return (
        [q(1, "quant", "Quantitative Aptitude", "correct", "Ratio")]
        + [q(i, "quant", "Quantitative Aptitude", "wrong", "Ratio") for i in (2, 3, 4)]
        + [q(i, "quant", "Quantitative Aptitude", "skipped", "Mensuration") for i in (5, 6)]
        + [q(i, "english", "English Comprehension", "correct", "Synonym")
           for i in (7, 8, 9, 10)]
    )


@pytest.fixture
def card(sheet):
    return scoring.score(sheet, CONFIG)


# ------------------------------------------------------------------ weak areas

def test_low_accuracy_topic_is_flagged(card):
    weak = {w.topic for w in report_mod.weak_areas(card, CONFIG)}
    assert "Ratio" in weak


def test_mostly_skipped_topic_is_flagged(card):
    weak = {w.topic: w for w in report_mod.weak_areas(card, CONFIG)}
    assert "Mensuration" in weak
    assert "skipped" in weak["Mensuration"].reason


def test_strong_topic_is_not_flagged(card):
    assert "Synonym" not in {w.topic for w in report_mod.weak_areas(card, CONFIG)}


def test_weak_areas_are_sorted_by_marks_lost(card):
    weak = report_mod.weak_areas(card, CONFIG)
    assert [w.topic for w in weak] == ["Ratio", "Mensuration"]
    assert weak[0].marks_lost >= weak[1].marks_lost


def test_tiny_topics_are_ignored(card):
    """Mensuration has only 2 questions; raising the floor to 3 drops it."""
    cfg = {**CONFIG, "analysis": {**CONFIG["analysis"], "weak_topic_min_questions": 3}}
    assert [w.topic for w in report_mod.weak_areas(card, cfg)] == ["Ratio"]


def test_thresholds_come_from_config(card):
    strict = {**CONFIG, "analysis": {**CONFIG["analysis"], "weak_topic_accuracy": 0.0,
                                     "weak_topic_skip_rate": 1.0}}
    assert report_mod.weak_areas(card, strict) == []


def test_weakest_sections_are_by_marks_lost(card):
    assert [b.label for b in report_mod.weakest_sections(card, 2)] == [
        "Quantitative Aptitude", "English Comprehension"]


# ------------------------------------------------------------- attempt strategy

def test_low_attempt_rate_quotes_the_expected_gain():
    """4 attempted out of 10 is well under the 80% target, with good accuracy."""
    sheet = ([q(i, "quant", "Quantitative Aptitude", "correct", "Ratio") for i in (1, 2, 3)]
             + [q(4, "quant", "Quantitative Aptitude", "wrong", "Ratio")]
             + [q(i, "quant", "Quantitative Aptitude", "skipped", "Ratio")
                for i in range(5, 11)])
    advice = report_mod.attempt_advice(scoring.score(sheet, CONFIG), CONFIG)
    assert advice.below_target
    assert advice.attempt_rate == pytest.approx(0.40)
    assert advice.extra_to_hit_target == 4
    assert advice.per_question > 0
    assert "worth about" in advice.text


def test_attempt_rate_exactly_at_target_is_not_below_it(card):
    advice = report_mod.attempt_advice(card, CONFIG)
    assert advice.attempt_rate == pytest.approx(0.80)
    assert not advice.below_target


def test_attempt_advice_uses_the_candidates_own_accuracy(card):
    advice = report_mod.attempt_advice(card, CONFIG)
    o = card.overall
    assert advice.accuracy == pytest.approx(o.accuracy)
    assert advice.per_question == pytest.approx(o.accuracy * 2.0 + (1 - o.accuracy) * -0.5)


def test_guessing_is_discouraged_below_breakeven():
    sheet = ([q(i, "quant", "Quantitative Aptitude", "wrong", "Ratio") for i in range(1, 10)]
             + [q(10, "quant", "Quantitative Aptitude", "skipped", "Ratio")])
    advice = report_mod.attempt_advice(scoring.score(sheet, CONFIG), CONFIG)
    assert advice.per_question < 0
    assert "cost you" in advice.text


def test_high_attempt_rate_shifts_the_advice_to_accuracy():
    sheet = ([q(i, "quant", "Quantitative Aptitude", "correct", "Ratio") for i in range(1, 6)]
             + [q(i, "quant", "Quantitative Aptitude", "wrong", "Ratio") for i in range(6, 11)])
    advice = report_mod.attempt_advice(scoring.score(sheet, CONFIG), CONFIG)
    assert not advice.below_target
    assert "accuracy, not volume" in advice.text


# -------------------------------------------------------------------- trends

def _history(rows):
    return pd.DataFrame(rows, columns=scoring.HISTORY_COLUMNS)


def test_trend_is_unavailable_without_history(card):
    assert report_mod.trend(None, "Mock 2", card.marking).available is False
    assert report_mod.trend(pd.DataFrame(), "Mock 2", card.marking).available is False


def test_trend_with_one_mock_only_is_unavailable(sheet, card):
    history = scoring.questions_frame(sheet, "2026-10-28", "Mock 1")
    assert report_mod.trend(history, "Mock 1", card.marking).available is False


def test_trend_reports_score_and_accuracy_change(sheet, card):
    old = scoring.questions_frame(sheet, "2026-10-01", "Mock 1")
    better = [q(i, "quant", "Quantitative Aptitude", "correct", "Ratio") for i in range(1, 11)]
    new = scoring.questions_frame(better, "2026-10-28", "Mock 2")
    history = scoring.merge_history(old, new)
    t = report_mod.trend(history, "Mock 2", card.marking)
    assert t.available
    assert t.previous_name == "Mock 1"
    assert t.score_change == pytest.approx(20.0 - 8.5)
    assert t.accuracy_change > 0
    assert "up" in t.text


def test_trend_detects_a_decline(sheet, card):
    good = [q(i, "quant", "Quantitative Aptitude", "correct", "Ratio") for i in range(1, 11)]
    history = scoring.merge_history(
        scoring.questions_frame(good, "2026-10-01", "Mock 1"),
        scoring.questions_frame(sheet, "2026-10-28", "Mock 2"))
    t = report_mod.trend(history, "Mock 2", card.marking)
    assert t.score_change < 0
    assert "down" in t.text


def test_trend_lists_only_topics_that_actually_moved(sheet):
    marking = {"correct": 2.0, "wrong": -0.5, "skipped": 0.0}
    before = [q(1, "quant", "Quantitative Aptitude", "correct", "Ratio"),
              q(2, "quant", "Quantitative Aptitude", "wrong", "Ratio"),
              q(3, "english", "English Comprehension", "correct", "Synonym"),
              q(4, "reasoning", "Reasoning", "correct", "Series")]
    after = [q(1, "quant", "Quantitative Aptitude", "correct", "Ratio"),
             q(2, "quant", "Quantitative Aptitude", "correct", "Ratio"),
             q(3, "english", "English Comprehension", "correct", "Synonym"),
             q(4, "reasoning", "Reasoning", "wrong", "Series")]
    history = scoring.merge_history(
        scoring.questions_frame(before, "2026-10-01", "Mock 1"),
        scoring.questions_frame(after, "2026-10-28", "Mock 2"))
    t = report_mod.trend(history, "Mock 2", marking)
    assert [name for name, *_ in t.improved_topics] == ["Ratio"]
    assert [name for name, *_ in t.declined_topics] == ["Series"]


def test_merge_history_replaces_a_rerun_of_the_same_mock(sheet):
    first = scoring.questions_frame(sheet, "2026-10-28", "Mock 1")
    again = scoring.questions_frame(sheet[:4], "2026-10-28", "Mock 1")
    merged = scoring.merge_history(first, again)
    assert len(merged) == 4


def test_read_history_rejects_a_foreign_csv(tmp_path):
    path = tmp_path / "other.csv"
    path.write_text("a,b\n1,2\n")
    with pytest.raises(scoring.HistoryError):
        scoring.read_history(path)


def test_read_history_round_trips(sheet, tmp_path):
    path = tmp_path / "history.csv"
    scoring.questions_frame(sheet, "2026-10-28", "Mock 1").to_csv(path, index=False)
    loaded = scoring.read_history(path)
    assert len(loaded) == 10
    assert list(loaded.columns) == scoring.HISTORY_COLUMNS


def test_read_history_rejects_an_unknown_status(sheet, tmp_path):
    df = scoring.questions_frame(sheet, "2026-10-28", "Mock 1")
    df.loc[0, "status"] = "maybe"
    path = tmp_path / "history.csv"
    df.to_csv(path, index=False)
    with pytest.raises(scoring.HistoryError):
        scoring.read_history(path)


# --------------------------------------------------------------------- plan

def test_plan_has_seven_days(card):
    weak = report_mod.weak_areas(card, CONFIG)
    plan = report_mod.study_plan(card, weak, TEMPLATES)
    assert [d.day for d in plan] == list(range(1, 8))


def test_plan_names_the_weakest_topics(card):
    weak = report_mod.weak_areas(card, CONFIG)
    plan = report_mod.study_plan(card, weak, TEMPLATES)
    text = " ".join(t + b for d in plan for t, b in d.blocks)
    assert "Ratio" in text
    assert "Mensuration" in text


def test_plan_allocates_the_two_weakest_sections_first(card):
    weak = report_mod.weak_areas(card, CONFIG)
    plan = report_mod.study_plan(card, weak, TEMPLATES)
    day1 = " ".join(t for t, _ in plan[0].blocks)
    assert "Quantitative Aptitude" in day1


def test_every_day_after_the_first_reviews_the_day_before(card):
    weak = report_mod.weak_areas(card, CONFIG)
    plan = report_mod.study_plan(card, weak, TEMPLATES)
    for day in plan[1:6]:
        titles = " ".join(t for t, _ in day.blocks)
        assert f"Review Day {day.day - 1}" in titles


def test_plan_survives_a_sheet_with_no_weak_topics():
    perfect = [q(i, "quant", "Quantitative Aptitude", "correct", "Ratio") for i in range(1, 11)]
    card = scoring.score(perfect, CONFIG)
    plan = report_mod.study_plan(card, [], TEMPLATES)
    assert len(plan) == 7
    assert all(d.blocks for d in plan)


# ------------------------------------------------------------- wrong answers

def test_wrong_answer_list_shows_both_options(sheet):
    df = report_mod.wrong_answers(sheet)
    assert len(df) == 3
    assert df.iloc[0]["You answered"].startswith("A.")
    assert df.iloc[0]["Correct answer"].startswith("B.")


def test_skipped_list_has_no_selected_column(sheet):
    df = report_mod.skipped_answers(sheet)
    assert len(df) == 2
    assert "You answered" not in df.columns


# ------------------------------------------------------------------ markdown

@pytest.fixture
def rendered(sheet, card):
    rep = report_mod.build(sheet, card, CONFIG, SheetMeta(candidate_name="ANJALI YADAV"),
                           "Mock 1", history=None, templates=TEMPLATES)
    return report_mod.to_markdown(rep, TEMPLATES)


def test_markdown_has_all_six_sections(rendered):
    for heading in ("## 1. Score summary", "## 2. Weak areas", "## 3. Attempt strategy",
                    "## 4. Trend", "## 5. Study plan", "## 6. Wrong answers"):
        assert heading in rendered


def test_markdown_states_the_score(rendered):
    assert "5 / 20" in rendered


def test_markdown_escapes_pipes_in_question_text():
    sheet = [q(1, "quant", "Quantitative Aptitude", "wrong", "Ratio")]
    sheet[0].question_text = "Which is correct | or wrong?"
    card = scoring.score(sheet, CONFIG)
    rep = report_mod.build(sheet, card, CONFIG, SheetMeta(), "Mock 1", templates=TEMPLATES)
    md = report_mod.to_markdown(rep, TEMPLATES)
    assert r"correct \| or wrong" in md


def test_full_pipeline_on_the_sample_sheet(parsed, config):
    card = scoring.score(parsed.questions, config)
    rep = report_mod.build(parsed.questions, card, config, parsed.meta, "Mock 1")
    md = report_mod.to_markdown(rep)
    assert "ANJALI YADAV" in md
    assert "85 / 200" in md
    assert len(rep.wrong) == 30
    assert len(rep.plan) == 7
