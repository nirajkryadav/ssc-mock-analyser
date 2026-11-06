"""Topic tagging: keyword matching, first-match ordering and the unmatched log."""
import csv
import io

import pytest

import topics as topics_mod
from parser import Question


TOPICS = topics_mod.load_topics()


def make_q(q_no, section, text, options=None, section_name="Section"):
    return Question(q_no=q_no, section=section, section_name=section_name, part="PART-X",
                    question_text=text, options=options or ["a", "b", "c", "d"],
                    selected_index=0, correct_index=0, status="correct")


@pytest.mark.parametrize("section, text, expected", [
    ("quant", "Find the simple interest on Rs. 8000 at 5% per annum.", "Simple and Compound Interest"),
    ("quant", "A shopkeeper sells an article at a profit of 20%.", "Profit and Loss"),
    ("quant", "The ratio of two numbers is 3 : 5.", "Ratio and Proportion"),
    ("quant", "Find the area of a circle whose radius is 14 cm.", "Mensuration"),
    ("quant", "Solve the simultaneous equations for x.", "Algebra"),
    ("quant", "Find the value of sin 30 + cos 60.", "Trigonometry"),
    ("general_awareness", "Through which states does the River Kaveri flow?", "Geography"),
    ("general_awareness", "Which Article of the Constitution deals with equality?", "Polity"),
    ("general_awareness", "The Dronacharya Award is given in which sport?", "Awards and Honours"),
    ("general_awareness", "Who chaired the committee on banking reform?", "Current Affairs"),
    ("reasoning", 'In a certain code language, "PEACE" is written as "MBXZB".', "Coding-Decoding"),
    ("reasoning", "A is the brother of B. How is A related to C?", "Blood Relation"),
    ("reasoning", "Walking towards north, he turns left after 3 km.", "Direction Sense"),
    ("reasoning", "Select the venn diagram that best represents the relationship.", "Venn Diagram"),
    ("english", "Select the most appropriate synonym of ABUNDANT.", "Synonym"),
    ("english", "Select the most appropriate antonym of CONCEAL.", "Antonym"),
    ("english", 'Meaning of the idiom "to let the cat out of the bag".', "Idioms and Phrases"),
    ("english", "Select the segment that contains a grammatical error.", "Error Spotting"),
])
def test_keyword_matches(section, text, expected):
    assert topics_mod.match_topic(make_q(1, section, text), TOPICS) == expected


def test_options_are_searched_too():
    """Some questions carry the only clue in the options, not the stem."""
    q = make_q(1, "general_awareness", "Which of the following is correct?",
               options=["Karnataka river basin", "x", "y", "z"])
    assert topics_mod.match_topic(q, TOPICS) == "Geography"


def test_matching_is_case_insensitive():
    lower = make_q(1, "english", "select the most appropriate SYNONYM of the word")
    assert topics_mod.match_topic(lower, TOPICS) == "Synonym"


def test_first_match_wins_in_yaml_order():
    """Both 'article' (Polity) and 'budget' (Economy) appear; Polity is listed first."""
    q = make_q(1, "general_awareness", "The Union Budget is presented under which Article?")
    assert topics_mod.match_topic(q, TOPICS) == "Polity"


def test_unknown_question_falls_through_to_unclassified():
    q = make_q(1, "reasoning", "Zzzz qqqq wwww.", options=["1", "2", "3", "4"])
    assert topics_mod.match_topic(q, TOPICS) == topics_mod.UNMATCHED


def test_unknown_section_is_unclassified():
    q = make_q(1, "astrology", "Which planet governs Tuesday?")
    assert topics_mod.match_topic(q, TOPICS) == topics_mod.UNMATCHED


def test_tag_questions_sets_topic_in_place():
    qs = [make_q(1, "english", "Select the synonym of ABUNDANT."),
          make_q(2, "reasoning", "Zzzz qqqq.")]
    topics_mod.tag_questions(qs, TOPICS)
    assert qs[0].topic == "Synonym"
    assert qs[1].topic == topics_mod.UNMATCHED


def test_unmatched_csv_lists_only_unmatched_questions():
    qs = [make_q(1, "english", "Select the synonym of ABUNDANT."),
          make_q(2, "reasoning", "Zzzz qqqq.")]
    topics_mod.tag_questions(qs, TOPICS)
    rows = list(csv.DictReader(io.StringIO(topics_mod.unmatched_csv(qs))))
    assert [r["q_no"] for r in rows] == ["2"]


def test_write_unmatched_creates_a_file(tmp_path):
    qs = [make_q(2, "reasoning", "Zzzz qqqq.")]
    topics_mod.tag_questions(qs, TOPICS)
    path = tmp_path / "unmatched.csv"
    assert topics_mod.write_unmatched(qs, path) == 1
    assert "Zzzz" in path.read_text()


def test_the_sample_sheet_is_fully_classified(parsed):
    """If the dictionaries regress, the sample sheet is the first thing to notice."""
    unmatched = topics_mod.unmatched_rows(parsed.questions)
    assert unmatched == [], f"{len(unmatched)} unclassified: {[r['q_no'] for r in unmatched]}"


def test_every_section_in_yaml_has_topics():
    for section, mapping in TOPICS.items():
        assert mapping, f"{section} has no topics"
        for topic, keywords in mapping.items():
            assert keywords, f"{section}/{topic} has no keywords"
