"""Build the analysis report from the scorecard. Rules only — no model calls.

Every number here comes from the candidate's own sheet, and every sentence is a
template in plan_templates.yaml or a f-string below. Nothing is generated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import yaml

import scoring
import topics as topics_mod
from scoring import Breakdown, ScoreCard

TEMPLATES_PATH = Path(__file__).with_name("plan_templates.yaml")


def load_templates(path: str | Path | None = None) -> dict[str, Any]:
    with open(path or TEMPLATES_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------- weak areas


@dataclass
class WeakArea:
    topic: str
    section: str
    reason: str
    questions: int
    attempted: int
    correct: int
    skipped: int
    accuracy: float
    marks_lost: float


def weak_areas(card: ScoreCard, config: dict[str, Any]) -> list[WeakArea]:
    """Topics worth spending the next week on, heaviest loss first.

    A topic qualifies on either of two counts: the candidate gets it wrong more
    often than not when they attempt it, or they skip most of it. Both are
    weaknesses, but they need different fixes, so the reason is recorded.
    """
    rules = config["analysis"]
    min_q = int(rules["weak_topic_min_questions"])
    acc_floor = float(rules["weak_topic_accuracy"])
    skip_ceiling = float(rules["weak_topic_skip_rate"])

    out: list[WeakArea] = []
    for b in card.topics:
        # "Unclassified" is a gap in the keyword dictionaries, not a weakness in
        # the candidate. Reporting it as the thing to study would be nonsense.
        if b.label == topics_mod.UNMATCHED or b.total < min_q:
            continue
        low_accuracy = b.attempted > 0 and b.accuracy < acc_floor
        high_skips = b.skip_rate > skip_ceiling
        if not (low_accuracy or high_skips):
            continue
        if low_accuracy and high_skips:
            reason = (f"{b.accuracy:.0%} accuracy when attempted, and "
                      f"{b.skipped} of {b.total} skipped")
        elif low_accuracy:
            reason = f"{b.correct} right out of {b.attempted} attempted ({b.accuracy:.0%})"
        else:
            reason = f"{b.skipped} of {b.total} skipped — barely attempted"
        out.append(WeakArea(
            topic=b.label, section=card.topic_sections.get(b.label, ""), reason=reason,
            questions=b.total, attempted=b.attempted, correct=b.correct,
            skipped=b.skipped, accuracy=b.accuracy, marks_lost=b.marks_lost))
    out.sort(key=lambda w: (-w.marks_lost, w.topic))
    return out


def weakest_sections(card: ScoreCard, n: int = 2) -> list[Breakdown]:
    """The n sections losing the most marks."""
    return sorted(card.sections, key=lambda b: (-b.marks_lost, b.label))[:n]


# ---------------------------------------------------------------- attempt advice


@dataclass
class AttemptAdvice:
    below_target: bool
    attempt_rate: float
    target: float
    skipped: int
    accuracy: float
    per_question: float
    expected_total: float
    breakeven: float
    extra_to_hit_target: int
    expected_from_target: float
    text: str


def attempt_advice(card: ScoreCard, config: dict[str, Any]) -> AttemptAdvice:
    """What the candidate would gain by attempting more of what they left blank."""
    target = float(config["analysis"]["target_attempt_rate"])
    o = card.overall
    gain = scoring.expected_gain_from_guessing(card)
    extra = max(0, int(round(target * o.total)) - o.attempted)
    extra = min(extra, o.skipped)
    expected_from_target = extra * gain["per_question"]

    if o.attempt_rate >= target:
        text = (f"Your attempt rate is {o.attempt_rate:.0%}, at or above the "
                f"{target:.0%} mark. The gain now is in accuracy, not volume: "
                f"the {o.wrong} wrong answers cost you "
                f"{abs(o.wrong * card.marking['wrong']):.1f} marks on top of the "
                f"{o.wrong * card.marking['correct']:.0f} you did not earn.")
    elif gain["per_question"] <= 0:
        text = (f"Your attempt rate is {o.attempt_rate:.0%}, below the {target:.0%} "
                f"mark, but at {o.accuracy:.0%} accuracy guessing would cost you "
                f"marks: you need {gain['breakeven_accuracy']:.0%} just to break even "
                f"under {card.marking['correct']:+g}/{card.marking['wrong']:+g}. "
                f"Fix accuracy first, then attempt more.")
    else:
        text = (f"Your attempt rate is {o.attempt_rate:.0%}, below the {target:.0%} "
                f"mark. At your own {o.accuracy:.0%} accuracy each extra attempt is "
                f"worth {gain['per_question']:+.2f} marks on average. Attempting "
                f"{extra} more of the {o.skipped} you left blank would be worth about "
                f"{expected_from_target:+.1f} marks; attempting all {o.skipped} would "
                f"be worth about {gain['expected_total']:+.1f}. "
                f"Break-even is {gain['breakeven_accuracy']:.0%} accuracy.")

    return AttemptAdvice(
        below_target=o.attempt_rate < target, attempt_rate=o.attempt_rate, target=target,
        skipped=o.skipped, accuracy=o.accuracy, per_question=gain["per_question"],
        expected_total=gain["expected_total"], breakeven=gain["breakeven_accuracy"],
        extra_to_hit_target=extra, expected_from_target=expected_from_target, text=text)


# ----------------------------------------------------------------------- trends


@dataclass
class Trend:
    available: bool
    previous_name: str = ""
    previous_date: str = ""
    score_change: float = 0.0
    accuracy_change: float = 0.0
    attempt_change: float = 0.0
    improved_topics: list[tuple[str, float, float]] = field(default_factory=list)
    declined_topics: list[tuple[str, float, float]] = field(default_factory=list)
    text: str = "No previous history was uploaded, so there is nothing to compare against."


def trend(history: pd.DataFrame, current_name: str,
          marking: dict[str, float]) -> Trend:
    """Compare this mock with the one before it in the merged history."""
    if history is None or history.empty:
        return Trend(available=False)
    mocks = scoring.mock_trend(history, marking)
    if len(mocks) < 2 or current_name not in set(mocks["mock_name"]):
        return Trend(available=False)

    idx = mocks.index[mocks["mock_name"] == current_name][-1]
    pos = mocks.index.get_loc(idx)
    if pos == 0:
        return Trend(available=False)
    now, before = mocks.loc[idx], mocks.iloc[pos - 1]

    d_score = float(now["score"]) - float(before["score"])
    d_acc = float(now["accuracy"]) - float(before["accuracy"])
    d_att = float(now["attempt_rate"]) - float(before["attempt_rate"])

    topics = scoring.topic_trend(history)
    now_t = topics[topics["mock_name"] == current_name].set_index("topic")["accuracy"]
    prev_t = topics[topics["mock_name"] == before["mock_name"]].set_index("topic")["accuracy"]
    shared = [t for t in now_t.index if t in prev_t.index
              and pd.notna(now_t[t]) and pd.notna(prev_t[t])]
    changes = sorted(((t, float(prev_t[t]), float(now_t[t])) for t in shared),
                     key=lambda r: r[2] - r[1])

    direction = "up" if d_score > 0 else ("down" if d_score < 0 else "level")
    text = (f"Against {before['mock_name']} ({before['mock_date']}), your score is "
            f"{direction} {abs(d_score):.1f} marks ({before['score']:.1f} → "
            f"{now['score']:.1f}). Accuracy {d_acc:+.1f} points "
            f"({before['accuracy']:.1f}% → {now['accuracy']:.1f}%), attempt rate "
            f"{d_att:+.1f} points ({before['attempt_rate']:.1f}% → "
            f"{now['attempt_rate']:.1f}%).")

    return Trend(
        available=True, previous_name=str(before["mock_name"]),
        previous_date=str(before["mock_date"]), score_change=d_score,
        accuracy_change=d_acc, attempt_change=d_att,
        improved_topics=list(reversed([c for c in changes if c[2] > c[1]][-3:])),
        declined_topics=[c for c in changes if c[2] < c[1]][:3],
        text=text)


# -------------------------------------------------------------------- study plan


@dataclass
class PlanDay:
    day: int
    focus: str
    blocks: list[tuple[str, str]]


def study_plan(card: ScoreCard, weak: Sequence[WeakArea],
               templates: dict[str, Any] | None = None) -> list[PlanDay]:
    """Seven days built from fixed templates, with topic names filled in."""
    tpl = templates or load_templates()
    plan_cfg = tpl["plan"]
    sections = weakest_sections(card, 2)
    section_a = sections[0].label if sections else "Quantitative Aptitude"
    section_b = sections[1].label if len(sections) > 1 else section_a

    topic_a = weak[0].topic if weak else _fallback_topic(card, section_a)
    topic_b = weak[1].topic if len(weak) > 1 else _fallback_topic(card, section_b)
    questions = int(plan_cfg["block_questions"])

    base = {
        "section_a": section_a, "section_b": section_b,
        "topic_a": topic_a, "topic_b": topic_b, "topic": topic_a,
        "section": section_a,
        "minutes": plan_cfg["block_minutes"],
        "review_minutes": plan_cfg["review_minutes"],
        "questions": questions,
        # A realistic step up, not perfection: 70% of the block.
        "target": max(1, int(round(questions * 0.7))),
    }

    days: list[PlanDay] = []
    for day in range(1, int(plan_cfg["days"]) + 1):
        ctx = dict(base, day=day, yesterday=day - 1)
        # Rotate through the weak list so later days are not all the same topic.
        if weak:
            ctx["topic"] = weak[(day - 1) % len(weak)].topic
            ctx["topic_a"] = weak[(day - 1) % len(weak)].topic
            if len(weak) > 1:
                ctx["topic_b"] = weak[day % len(weak)].topic
        blocks = []
        for key in tpl["schedule"][day]:
            block = tpl["blocks"][key]
            blocks.append((block["title"].format(**ctx), block["body"].format(**ctx)))
        days.append(PlanDay(day=day, focus=tpl["focus"][day].format(**ctx), blocks=blocks))
    return days


def _fallback_topic(card: ScoreCard, section_name: str) -> str:
    """When nothing is weak enough to flag, practise the costliest topic anyway."""
    in_section = [b for b in card.topics
                  if card.topic_sections.get(b.label) == section_name
                  and b.label != topics_mod.UNMATCHED]
    pool = in_section or [b for b in card.topics if b.label != topics_mod.UNMATCHED]
    return pool[0].label if pool else "mixed practice"


# ------------------------------------------------------------------ wrong answers


def wrong_answers(questions: Sequence[Any]) -> pd.DataFrame:
    rows = [{
        "Q.No": q.q_no,
        "Section": q.section_name,
        "Topic": q.topic or "Unclassified",
        "Question": q.question_text,
        "You answered": q.selected_label,
        "Correct answer": q.correct_label,
    } for q in questions if q.status == "wrong"]
    return pd.DataFrame(rows, columns=["Q.No", "Section", "Topic", "Question",
                                       "You answered", "Correct answer"])


def skipped_answers(questions: Sequence[Any]) -> pd.DataFrame:
    rows = [{
        "Q.No": q.q_no,
        "Section": q.section_name,
        "Topic": q.topic or "Unclassified",
        "Question": q.question_text,
        "Correct answer": q.correct_label,
    } for q in questions if q.status == "skipped"]
    return pd.DataFrame(rows, columns=["Q.No", "Section", "Topic", "Question",
                                       "Correct answer"])


# ------------------------------------------------------------------- the report


@dataclass
class Report:
    card: ScoreCard
    weak: list[WeakArea]
    advice: AttemptAdvice
    trend: Trend
    plan: list[PlanDay]
    wrong: pd.DataFrame
    skipped: pd.DataFrame
    meta: Any
    mock_name: str
    unclassified: int = 0


def build(questions: Sequence[Any], card: ScoreCard, config: dict[str, Any],
          meta: Any, mock_name: str, history: pd.DataFrame | None = None,
          templates: dict[str, Any] | None = None) -> Report:
    weak = weak_areas(card, config)
    return Report(
        card=card,
        weak=weak,
        advice=attempt_advice(card, config),
        trend=trend(history, mock_name, card.marking) if history is not None else Trend(False),
        plan=study_plan(card, weak, templates),
        wrong=wrong_answers(questions),
        skipped=skipped_answers(questions),
        meta=meta,
        mock_name=mock_name,
        unclassified=sum(1 for q in questions
                         if (q.topic or topics_mod.UNMATCHED) == topics_mod.UNMATCHED),
    )


def to_markdown(report: Report, templates: dict[str, Any] | None = None) -> str:
    tpl = templates or load_templates()
    card = report.card
    o = card.overall
    m = report.meta
    L: list[str] = []

    L.append(f"# SSC CGL mock analysis — {report.mock_name}")
    L.append("")
    who = [x for x in (getattr(m, "candidate_name", ""), getattr(m, "roll_no", ""),
                       getattr(m, "test_date", ""), getattr(m, "shift", "")) if x]
    if who:
        L.append(" · ".join(who))
        L.append("")

    # 1 ----------------------------------------------------------------- summary
    L.append("## 1. Score summary")
    L.append("")
    L.append(f"**{o.score:g} / {o.max_score:g}**  "
             f"({o.correct} correct, {o.wrong} wrong, {o.skipped} skipped)")
    L.append("")
    L.append(f"Accuracy **{o.accuracy:.1%}** on the {o.attempted} you attempted · "
             f"attempt rate **{o.attempt_rate:.1%}** · marking "
             f"{card.marking['correct']:+g} / {card.marking['wrong']:+g}")
    L.append("")
    L.append("| Section | Score | Correct | Wrong | Skipped | Accuracy | Attempted |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for b in card.sections:
        L.append(f"| {b.label} | {b.score:g} / {b.max_score:g} | {b.correct} | {b.wrong} "
                 f"| {b.skipped} | {b.accuracy:.0%} | {b.attempt_rate:.0%} |")
    L.append("")

    # 2 -------------------------------------------------------------- weak areas
    L.append("## 2. Weak areas")
    L.append("")
    if not report.weak:
        L.append("No topic fell below the thresholds in `config.yaml`. The marks are "
                 "spread evenly rather than concentrated in one place.")
    else:
        L.append("Sorted by marks lost — the top of this list is where a week of work "
                 "pays best.")
        L.append("")
        L.append("| Topic | Section | Marks lost | Why |")
        L.append("|---|---|---:|---|")
        for w in report.weak:
            L.append(f"| {w.topic} | {w.section} | {w.marks_lost:g} | {w.reason} |")
    if report.unclassified:
        L.append("")
        L.append(f"*{report.unclassified} question(s) matched no keyword in "
                 f"topics.yaml and are excluded from this table. They are listed in "
                 f"the unmatched.csv download.*")
    L.append("")

    # 3 --------------------------------------------------------- attempt strategy
    L.append("## 3. Attempt strategy")
    L.append("")
    L.append(report.advice.text)
    L.append("")

    # 4 ------------------------------------------------------------------- trend
    L.append("## 4. Trend")
    L.append("")
    L.append(report.trend.text)
    if report.trend.available:
        if report.trend.improved_topics:
            L.append("")
            L.append("**Improved:** " + ", ".join(
                f"{t} ({a:.0f}% → {b:.0f}%)" for t, a, b in report.trend.improved_topics))
        if report.trend.declined_topics:
            L.append("")
            L.append("**Slipped:** " + ", ".join(
                f"{t} ({a:.0f}% → {b:.0f}%)" for t, a, b in report.trend.declined_topics))
    L.append("")

    # 5 ------------------------------------------------------------------ plan
    L.append("## 5. Study plan — next 7 days")
    L.append("")
    sections = weakest_sections(card, 2)
    L.append(tpl["headers"]["intro"].format(
        section_a=sections[0].label,
        section_b=sections[1].label if len(sections) > 1 else sections[0].label).strip())
    L.append("")
    for day in report.plan:
        L.append("### " + tpl["headers"]["day"].format(day=day.day, focus=day.focus))
        L.append("")
        for title, body in day.blocks:
            L.append(f"- **{title}** — {body}")
        L.append("")

    # 6 ---------------------------------------------------------- wrong answers
    L.append("## 6. Wrong answers")
    L.append("")
    if report.wrong.empty:
        L.append("Nothing wrong on this paper.")
    else:
        L.append(f"{len(report.wrong)} questions, costing "
                 f"{abs(len(report.wrong) * card.marking['wrong']):g} marks in negatives "
                 f"and {len(report.wrong) * card.marking['correct']:g} in marks not earned.")
        L.append("")
        L.append("| Q | Topic | Question | You answered | Correct answer |")
        L.append("|---:|---|---|---|---|")
        for _, r in report.wrong.iterrows():
            L.append(f"| {r['Q.No']} | {r['Topic']} | {_cell(r['Question'])} "
                     f"| {_cell(r['You answered'])} | {_cell(r['Correct answer'])} |")
    L.append("")

    if not report.skipped.empty:
        L.append("### Skipped questions")
        L.append("")
        L.append("| Q | Topic | Question | Correct answer |")
        L.append("|---:|---|---|---|")
        for _, r in report.skipped.iterrows():
            L.append(f"| {r['Q.No']} | {r['Topic']} | {_cell(r['Question'])} "
                     f"| {_cell(r['Correct answer'])} |")
        L.append("")

    return "\n".join(L).rstrip() + "\n"


def _cell(text: Any, limit: int = 160) -> str:
    """Make a string safe for a Markdown table cell."""
    s = str(text).replace("|", "\\|").replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"
