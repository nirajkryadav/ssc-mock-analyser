import syllabus as sy


def test_syllabus_covers_every_section_with_unique_items():
    syl = sy.load_syllabus()
    assert set(syl) == {"reasoning", "general_awareness", "quant", "english"}
    ids = [i for topics in syl.values() for t in topics for i in t.ids()]
    assert len(ids) == len(set(ids)) > 200
    assert all(t.subtopics for topics in syl.values() for t in topics)


def test_every_mock_topic_label_maps_to_a_syllabus_topic():
    import topics as topics_mod
    syl = sy.load_syllabus()
    tagged = topics_mod.load_topics()
    for section, labels in tagged.items():
        if section not in syl:
            continue
        mapped = {m for t in syl[section] for m in t.mocks}
        missing = set(labels) - mapped
        assert not missing, f"{section}: {missing}"


def test_counts_and_mock_stats():
    t = sy.Topic("quant", "Algebra", ["A", "B"], ["Algebra"])
    assert sy.count([t], {"quant/Algebra/A"}) == (1, 2)
    recs = [{"topics": [{"label": "Algebra", "section": "quant", "total": 4, "correct": 1,
                         "wrong": 2, "skipped": 1, "marks_lost": 3.0}]}] * 2
    s = sy.topic_mock_stats(t, sy.mock_stats(recs))
    assert s["questions"] == 8 and s["wrong"] == 4 and round(s["accuracy"], 2) == 0.33
    assert sy.is_weak(s) is True
    assert sy.topic_mock_stats(sy.Topic("quant", "Geometry", ["x"], ["Geometry"]),
                               sy.mock_stats(recs)) is None
