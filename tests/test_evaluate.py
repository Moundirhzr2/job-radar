import math

import pytest

from job_radar.evaluate import (
    item_key,
    mrr_at,
    ndcg_at,
    precision_at,
    recall_at,
    score,
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
