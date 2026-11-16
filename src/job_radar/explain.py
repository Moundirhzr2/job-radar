"""One search, step by step: what each stage of the RAG produced, and how long it took.

radar explain "alternance data analyst" prints, in Markdown:
1. Rewriting: what the language model understood (validated structure).
2. Retrieval: how many offers the area holds, and the best ones with their rank by meaning
   (pgvector), their rank by words (full text) and the fused score (RRF).
3. Re-ranking: the cross-encoder's relevance, and how far each offer moved.
4. Confidence: the decision taken on the best score.
5. Generation: the model compares the CV with the retrieved offer it ranked first; every quote
   is looked up in the offer and in the CV. The CV's own words are not printed: only whether
   each quote was found, so the trace can be shared.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .fit import offer_text
from .models import Kind
from .pipeline import CONFIDENCE
from .rerank import rerank
from .rewrite import RewrittenQuery
from .search import SearchQuery, search

STATUS = {"covered": "acquis", "to_confirm": "à confirmer", "missing": "manque"}


def explain(
    conn,
    request: str,
    town,
    radius_km: float,
    embedder,
    reranker,
    rewriter: Callable | None,
    profile: str,
    analyse: Callable | None,
    model: str,
    shown: int = 10,
) -> str:
    out: list[str] = [f"# « {request} » — {town.name}, {_radius(radius_km)}", ""]
    clock = time.perf_counter

    t = clock()
    if rewriter:
        rw = rewriter(request, profile)
        out += [f"## 1. Réécriture par le modèle de langage ({model}) — {clock() - t:.1f} s", ""]
    else:  # the default: measured, a small local model's rewriting lowered nDCG@10
        rw = RewrittenQuery(semantic_query=request, keywords=[request], kinds=[], town=None,
                            radius_km=None)  # fmt: skip
        out += ["## 1. Réécriture — désactivée (par défaut : voir la mesure)", ""]
    out += [
        f"- Texte encodé en vecteur : « {rw.semantic_query} »",
        f"- Mots-clés (recherche plein texte) : {', '.join(rw.keywords)}",
        f"- Contrats : {', '.join(rw.kinds) or 'tous'}",
        "",
    ]

    query = SearchQuery(rw.semantic_query, town.latitude, town.longitude, radius_km,
                        tuple(rw.keywords), tuple(Kind(k) for k in rw.kinds))  # fmt: skip
    (in_area,) = conn.execute(
        "SELECT count(*) FROM offers WHERE closed_at IS NULL AND embedding IS NOT NULL"
        " AND ST_DWithin(location, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)"
        " AND (%s::text[] IS NULL OR kinds && %s::text[])",
        (town.longitude, town.latitude, radius_km * 1000, list(rw.kinds) or None,
         list(rw.kinds) or None),
    ).fetchone()  # fmt: skip
    t = clock()
    vector = embedder.query(rw.semantic_query)
    found = search(conn, query, vector, pool=100, limit=50)
    out += [
        f"## 2. Recherche hybride — {clock() - t:.1f} s",
        "",
        f"{in_area} offres indexées dans la zone ; vecteur de {len(vector)} dimensions ; "
        f"{len(found)} candidates gardées après fusion des deux classements (RRF).",
        "",
        "| # | Offre | Rang sens | Rang mots | Score RRF |",
        "|--:|---|--:|--:|--:|",
    ]
    for i, c in enumerate(found[:shown], 1):
        out.append(f"| {i} | {_offer(c)} | {c.vector_rank or '–'} | {c.text_rank or '–'} | "
                   f"{c.score:.4f} |")  # fmt: skip
    out.append("")

    t = clock()
    ranked = rerank(found, rw.semantic_query, reranker, 50)
    out += [
        f"## 3. Re-ranking (cross-encoder) — {clock() - t:.1f} s",
        "",
        "| # | Offre | Pertinence | Rang avant |",
        "|--:|---|--:|--:|",
    ]
    for i, c in enumerate(ranked[:shown], 1):
        moved = c.extra["hybrid_rank"]
        out.append(f"| {i} | {_offer(c)} | {c.extra['rerank']:.2f} | {moved} |")
    best = ranked[0].extra["rerank"] if ranked else None
    verdict = (
        "rien de convaincant : le radar le dit au lieu de proposer une mauvaise offre"
        if best is None or best < CONFIDENCE
        else "réponse donnée"
    )
    out += [
        "",
        f"## 4. Confiance — meilleur score {best or 0:.2f}, seuil {CONFIDENCE} : {verdict}",
        "",
    ]

    if analyse and ranked:
        top = ranked[0]
        text = offer_text(top.title, top.company, top.city, top.kinds, top.weekly_hours,
                          top.description)  # fmt: skip
        t = clock()
        fit = analyse(text, profile)
        reqs = fit.requirements
        out += [
            f"## 5. Génération ancrée — {clock() - t:.1f} s",
            "",
            f"Le modèle compare le CV à l'offre retrouvée n° 1 ({_offer(top)}).",
            "",
            "| Exigence | Citation de l'offre | Dans l'offre | Statut | Preuve dans le CV |",
            "|---|---|:-:|---|:-:|",
        ]
        yes = {True: "oui", False: "non"}
        for r in reqs:
            proof = "–" if r.status == "missing" else yes[r.profile_evidence_found]
            out.append(
                f"| {r.skill} | « {_cell(r.evidence)} » | {yes[r.evidence_found]} | "
                f"{STATUS[r.status]} | {proof} |"
            )
        found_in_offer = sum(r.evidence_found for r in reqs)
        out += [
            "",
            f"{found_in_offer} citations sur {len(reqs)} retrouvées mot pour mot dans l'offre. "
            "Une compétence « acquise » sans preuve dans le CV repasse « à confirmer ».",
            "",
        ]
    return "\n".join(out)


def _radius(km: float) -> str:
    return "toute la France" if km >= 2000 else f"{km:g} km"


def _cell(text: str | None) -> str:
    return (text or "").replace("|", "/").replace("\n", " ")[:90]


def _offer(c) -> str:
    return _cell(f"{c.title} — {c.company or '?'}, {c.city}")
