"""The blind judging page: one local HTML file built from the judging sheets (radar eval page).

The judge sees each request and its offers in a fixed shuffled order, never which method found
them. Judgments stay in the browser while judging and are exported as one JSON file, which
`radar eval import` merges into eval/labels.json. Offer texts go in through text nodes only.
"""

from __future__ import annotations

import json
from pathlib import Path

PAGE = (Path(__file__).with_name("judge_page.html")).read_text("utf-8")


def page(sheets: list[dict]) -> str:
    """The page for these sheets, in their order. `</` is escaped so no offer text can close
    the script element that carries the data."""
    data = json.dumps(sorted(sheets, key=lambda s: s["position"]), ensure_ascii=False)
    return PAGE.replace("__DATA__", data.replace("</", "<\\/"))
