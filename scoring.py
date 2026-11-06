"""Turn parsed questions into marks and accuracy, per section and per topic."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import pandas as pd


@dataclass
class Breakdown:
    """Counts and marks for any slice of the paper."""
    label: str
    total: int = 0
    correct: int = 0
    wrong: int = 0
    skipped: int = 0
    score: float = 0.0

    @property
    def attempted(self) -> int:
        return self.correct + self.wrong

    @property
    def accuracy(self) -> float:
        """Share of attempted questions that were right."""
        return self.correct / self.attempted if self.attempted else 0.0

    @property
    def attempt_rate(self) -> float:
        return self.attempted / self.total if self.total else 0.0

    @property
    def skip_rate(self) -> float:
        return self.skipped / self.total if self.total else 0.0

    @property
    def marks_lost(self) -> float:
        """Marks forgone against a clean sweep of this slice."""
        return self.max_score - self.score

    max_score: float = 0.0


@dataclass
class ScoreCard:
    overall: Breakdown
    sections: list[Breakdown] = field(default_factory=list)
    topics: list[Breakdown] = field(default_factory=list)
    marking: dict[str, float] = field(default_factory=dict)
    # topic label -> section name, so the report can group topics by paper
    topic_sections: dict[str, str] = field(default_factory=dict)


def _tally(label: str, questions: Sequence[Any], marking: dict[str, float]) -> Breakdown:
    b = Breakdown(label=label, total=len(questions))
    for q in questions:
        if q.status == "correct":
            b.correct += 1
        elif q.status == "wrong":
            b.wrong += 1
        else:
            b.skipped += 1
    b.score = b.correct * marking["correct"] + b.wrong * marking["wrong"]
    b.max_score = b.total * marking["correct"]
    return b


def score(questions: Sequence[Any], config: dict[str, Any]) -> ScoreCard:
    marking = {k: float(v) for k, v in config["marking"].items()}
    card = ScoreCard(overall=_tally("Overall", questions, marking), marking=marking)

    for sec in config["sections"]:
        rows = [q for q in questions if q.section == sec["key"]]
        card.sections.append(_tally(sec["name"], rows, marking))

    seen: dict[str, list[Any]] = {}
    for q in questions:
        seen.setdefault(q.topic or "Unclassified", []).append(q)
    for topic, rows in seen.items():
        card.topics.append(_tally(topic, rows, marking))
        card.topic_sections[topic] = rows[0].section_name
    # Heaviest losses first — that is the order a candidate should read them in.
    card.topics.sort(key=lambda b: (-b.marks_lost, b.label))
    return card


def questions_frame(questions: Sequence[Any], mock_date: str,
                    mock_name: str = "") -> pd.DataFrame:
    """One row per question — the shape history.csv is stored in."""
    return pd.DataFrame([{
        "mock_date": mock_date,
        "mock_name": mock_name or mock_date,
        "q_no": q.q_no,
        "section": q.section_name,
        "topic": q.topic or "Unclassified",
        "status": q.status,
        "selected_option": q.selected_label,
        "correct_option": q.correct_label,
    } for q in questions])


def breakdown_frame(items: Sequence[Breakdown]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Label": b.label,
        "Questions": b.total,
        "Correct": b.correct,
        "Wrong": b.wrong,
        "Skipped": b.skipped,
        "Attempted": b.attempted,
        "Accuracy": round(b.accuracy * 100, 1),
        "Attempt rate": round(b.attempt_rate * 100, 1),
        "Score": round(b.score, 2),
        "Marks lost": round(b.marks_lost, 2),
    } for b in items])


def expected_gain_from_guessing(card: ScoreCard, accuracy: float | None = None) -> dict[str, float]:
    """Marks a candidate could expect from attempting what they left blank.

    Uses the candidate's own accuracy on attempted questions, not a 25% coin
    flip: someone who is right 70% of the time when they answer is a better
    guesser than the raw odds suggest, and someone below 20% is worse.
    """
    marking = card.marking
    acc = card.overall.accuracy if accuracy is None else accuracy
    per_question = acc * marking["correct"] + (1 - acc) * marking["wrong"]
    skipped = card.overall.skipped
    return {
        "accuracy_used": acc,
        "per_question": per_question,
        "skipped": skipped,
        "expected_total": per_question * skipped,
        "breakeven_accuracy": _breakeven(marking),
    }


def _breakeven(marking: dict[str, float]) -> float:
    """Accuracy at which guessing stops costing marks."""
    gain, penalty = marking["correct"], -marking["wrong"]
    denom = gain + penalty
    return penalty / denom if denom else 0.0


# ------------------------------------------------------------------------ history

HISTORY_COLUMNS = ["mock_date", "mock_name", "q_no", "section", "topic",
                   "status", "selected_option", "correct_option"]


class HistoryError(ValueError):
    """Raised when an uploaded history.csv is not in the expected shape."""


def read_history(source: Any) -> pd.DataFrame:
    """Load a previously downloaded history.csv.

    Only the columns this app writes are kept, and anything else is ignored, so
    a file the user has opened in Excel and added notes to still loads.
    """
    df = pd.read_csv(source, dtype=str)
    missing = [c for c in ("mock_date", "q_no", "section", "topic", "status")
               if c not in df.columns]
    if missing:
        raise HistoryError(
            "This does not look like a history.csv from this app — missing "
            f"column(s): {', '.join(missing)}."
        )
    for col in HISTORY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[HISTORY_COLUMNS].copy()
    df["q_no"] = pd.to_numeric(df["q_no"], errors="coerce").astype("Int64")
    df["mock_name"] = df["mock_name"].fillna("").replace("", pd.NA).fillna(df["mock_date"])
    bad = set(df["status"].dropna().unique()) - {"correct", "wrong", "skipped"}
    if bad:
        raise HistoryError(f"Unexpected status value(s) in history.csv: {sorted(bad)}")
    return df.dropna(subset=["q_no"])


def merge_history(previous: pd.DataFrame | None, current: pd.DataFrame) -> pd.DataFrame:
    """Append this mock to the uploaded history, replacing any same-named mock."""
    if previous is None or previous.empty:
        return current.copy()
    names = set(current["mock_name"].unique())
    kept = previous[~previous["mock_name"].isin(names)]
    return pd.concat([kept, current], ignore_index=True)


def mock_trend(history: pd.DataFrame, marking: dict[str, float]) -> pd.DataFrame:
    """Score, accuracy and attempt rate for every mock in the history."""
    if history.empty:
        return pd.DataFrame(columns=["mock_name", "mock_date", "questions", "correct",
                                     "wrong", "skipped", "score", "accuracy",
                                     "attempt_rate"])
    rows = []
    for (name, date), grp in history.groupby(["mock_name", "mock_date"], sort=False):
        counts = grp["status"].value_counts()
        correct = int(counts.get("correct", 0))
        wrong = int(counts.get("wrong", 0))
        skipped = int(counts.get("skipped", 0))
        attempted = correct + wrong
        rows.append({
            "mock_name": name,
            "mock_date": date,
            "questions": len(grp),
            "correct": correct,
            "wrong": wrong,
            "skipped": skipped,
            "score": round(correct * marking["correct"] + wrong * marking["wrong"], 2),
            "accuracy": round(100 * correct / attempted, 1) if attempted else 0.0,
            "attempt_rate": round(100 * attempted / len(grp), 1) if len(grp) else 0.0,
        })
    out = pd.DataFrame(rows).sort_values(["mock_date", "mock_name"], kind="stable")
    return out.reset_index(drop=True)


def topic_trend(history: pd.DataFrame) -> pd.DataFrame:
    """Accuracy per topic per mock — the table the Trends tab charts."""
    if history.empty:
        return pd.DataFrame(columns=["topic", "mock_name", "mock_date", "attempted",
                                     "correct", "accuracy"])
    rows = []
    for (topic, name, date), grp in history.groupby(
            ["topic", "mock_name", "mock_date"], sort=False):
        counts = grp["status"].value_counts()
        correct = int(counts.get("correct", 0))
        attempted = correct + int(counts.get("wrong", 0))
        rows.append({
            "topic": topic, "mock_name": name, "mock_date": date,
            "attempted": attempted, "correct": correct,
            "accuracy": round(100 * correct / attempted, 1) if attempted else None,
        })
    return pd.DataFrame(rows).sort_values(["topic", "mock_date"], kind="stable")
