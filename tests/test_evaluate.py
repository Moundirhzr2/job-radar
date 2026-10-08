import json
import math

import pytest

from job_radar.evaluate import (
    EvalQuery,
    import_labels,
    item_key,
    load_labels,
    mrr_at,
    ndcg_at,
    precision_at,
    recall_at,
    score,
    sheets,
    to_markdown,
)

LABELS = {
    "q__s__a": "yes",
    "q__s__b": "no",
    "q__s__c": "yes",
    "q__s__d": "unsure",
    "r__s__x": "no",  # a request without any relevant offer
}


def test_item_key_is_a_safe_document_id():
    assert item_key("data", "careers:jsonld", "abc/12 3") == "data__careers-jsonld__abc-12-3"


def test_measures_skip_unsure_offers():
    ranking = ["q__s__d", "q__s__b", "q__s__a", "q__s__c"]  # unsure first, ignored
    assert mrr_at(ranking, LABELS) == pytest.approx(1 / 2)
    assert precision_at(ranking, LABELS) == pytest.approx(2 / 3)
    ideal = 1 + 1 / math.log2(3)
    assert ndcg_at(ranking, LABELS, 2) == pytest.approx((1 / math.log2(3) + 1 / 2) / ideal)
    assert ndcg_at(["q__s__a", "q__s__c"], LABELS, 2) == pytest.approx(1.0)
    assert recall_at(["q__s__a", "q__s__z"], {"q__s__a", "q__s__c"}) == 0.5


def test_score_averages_over_requests_with_relevant_offers():
    rankings = {
        "A": {"q": ["q__s__a", "q__s__c", "q__s__b"], "r": ["r__s__x"]},
        "B": {"q": ["q__s__b", "q__s__a"], "r": ["r__s__x"]},
    }
    a, b = score(rankings, LABELS)
    assert (a.config, a.queries, a.mrr10, a.recall50) == ("A", 1, 1.0, 1.0)
    assert (b.mrr10, b.recall50) == (0.5, 0.5)
    table = to_markdown([a, b])
    assert "| A | 1.00 | 1.00 | 0.67 | 1.00 |" in table


def test_sheets_are_blind_and_stable():
    queries = [
        EvalQuery("q", "alternance data", "Mulhouse", 30, "Data"),
        EvalQuery("r", "x", "Colmar", 15),
    ]
    to_judge = [
        {
            "key": f"q__s__{i}",
            "query_id": "q",
            "request": "alternance data",
            "title": str(i),
            "found_by": ["hybride"],
        }
        for i in range(20)
    ] + [{"key": "r__s__x", "query_id": "r", "request": "x", "title": "x", "found_by": []}]
    out = sheets(queries, to_judge)
    q = out["q"]
    assert (q["label"], q["position"], out["r"]["label"]) == ("Data", 0, "r")
    assert all(set(it) == {"key", "title"} for it in q["items"])  # no configuration names
    order = [it["title"] for it in q["items"]]
    assert sorted(order, key=int) == [str(i) for i in range(20)]
    assert order != sorted(order, key=int)  # not in the order the methods ranked them
    assert order == [it["title"] for it in sheets(queries, list(reversed(to_judge)))["q"]["items"]]


def test_judgments_keep_who_judged_and_why(tmp_path):
    (tmp_path / "q__s__a.json").write_text('{"label": "yes"}')
    (tmp_path / "q__s__b.json").write_text('{"label": "no", "by": "claude", "note": "26 h"}')
    (tmp_path / "q__s__c.json").write_text('{"label": "maybe"}')  # not a judgment
    labels = import_labels(tmp_path)
    assert labels == {
        "q__s__a": {"label": "yes", "by": "moundir"},
        "q__s__b": {"label": "no", "by": "claude", "note": "26 h"},
    }
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(labels))
    assert load_labels(path) == {"q__s__a": "yes", "q__s__b": "no"}
    path.write_text('{"q__s__a": "unsure"}')  # the plain format still reads
    assert load_labels(path) == {"q__s__a": "unsure"}
