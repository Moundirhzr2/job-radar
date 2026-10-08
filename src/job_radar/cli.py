"""Command line: set the radar on a town and see what is around.

radar companies --town Mulhouse --radius 15 --naf 62,63
radar careers https://groupeoci.teamtailor.com/jobs
radar feed recruitee amiparis
radar offers --town Mulhouse --radius 30 --kind internship apprenticeship
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from . import db
from .models import Kind
from .sources.careers.ats import Board, fetch_board
from .sources.careers.fetch import PoliteFetcher
from .sources.careers.site import read_career_site
from .sources.companies import HEADCOUNT_LABEL, CompanyDirectory
from .sources.france_travail import CredentialsError, FranceTravail
from .sources.geo import Geocoder, GeocodingUnavailable, find_towns, place
from .sources.la_bonne_boite import ROME_FIELDS, LaBonneBoite

DEFAULT_DATABASE_URL = "postgresql://radar:radar@localhost:5433/radar"
KIND_LABELS = {
    "internship": "stage",
    "apprenticeship": "alternance",
    "student_job": "job étudiant",
    "job": "emploi",
}


def _connect():
    conn = db.connect(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    db.init_schema(conn)
    return conn


def _town(name: str):
    try:
        towns = find_towns(name, limit=1)
    except GeocodingUnavailable:
        sys.exit("Les services de géolocalisation ne répondent pas. Réessayez dans un instant.")
    if not towns:
        sys.exit(f"Commune introuvable : {name}")
    return towns[0]


def cmd_companies(args) -> None:
    town = _town(args.town)
    directory = CompanyDirectory()
    conn = _connect()
    n = db.upsert_companies(
        conn, directory.near(town.latitude, town.longitude, args.radius, args.sections, args.max)
    )
    naf = [p.strip() for p in args.naf.split(",")] if args.naf else None
    rows = db.companies_within(conn, town.latitude, town.longitude, args.radius, naf)
    print(f"{n} employeurs lus autour de {town.name} ({args.radius} km), {len(rows)} affichés.\n")
    for c in rows:
        size = HEADCOUNT_LABEL.get(c.headcount_range, "effectif inconnu")
        print(f"{c.distance_km:5.1f} km  {c.name[:50]:50}  {c.naf_code:7} {size:14} {c.city}")
        for officer in c.officers:
            print(f"{'':10}dirigeant : {officer}")


def _store(offers, label: str) -> None:
    geocoder = Geocoder()
    placing = place(offers, geocoder)
    conn = _connect()
    report = db.upsert_offers(conn, offers)
    print(
        f"{label} : {len(offers)} offres — {report.inserted} nouvelles, "
        f"{report.updated} modifiées, {report.unchanged} inchangées. "
        f"{placing.placed} placées au centre de leur commune, "
        f"{placing.unplaced} hors de France ou lieu inconnu."
    )
    retried = _place_pending(conn, geocoder)
    if placing.unavailable or retried:
        print(
            "Géolocalisation indisponible pour certaines offres : nouvel essai au prochain passage."
        )


def _place_pending(conn, geocoder: Geocoder) -> int:
    """Retry offers saved without a position. Returns how many are still waiting."""
    waiting = 0
    for offer_id, city, postal_code, country in db.unplaced_offers(conn):
        try:
            town = geocoder.locate(city, postal_code, country)
        except GeocodingUnavailable:
            waiting += 1
            continue
        if town:
            db.set_location(conn, offer_id, town.latitude, town.longitude)
    conn.commit()
    return waiting


def cmd_careers(args) -> None:
    report = read_career_site(PoliteFetcher(), args.url, max_job_pages=args.max_pages)
    if report.skipped:
        print(f"Non lues (interdites par robots.txt) : {len(report.skipped)}")
    _store(report.offers, args.url)


def cmd_feed(args) -> None:
    fetcher = PoliteFetcher()
    board = Board(args.provider, args.identifier, args.region)
    _store(
        fetch_board(board, lambda url: fetcher.get(url).json()),
        f"{args.provider}/{args.identifier}",
    )


def cmd_francetravail(args) -> None:
    if args.refresh:
        return _refresh_france_travail()
    if not args.town:
        sys.exit("Indiquer une commune (--town), ou --refresh pour relire les offres connues.")
    town = _town(args.town)
    kinds = [Kind(k) for k in args.kind] if args.kind else [None]
    ft = FranceTravail()
    offers = []
    try:
        for kind in kinds:
            offers.extend(ft.search(town.insee_code, args.radius, kind, args.keywords, args.max))
    except CredentialsError as exc:
        sys.exit(str(exc))
    _store(offers, f"France Travail autour de {town.name} ({args.radius:g} km)")


def _refresh_france_travail() -> None:
    """Re-read every known France Travail offer: current text and hours, or withdrawn."""
    conn = _connect()
    ids = db.open_offer_ids(conn, "france_travail")
    ft = FranceTravail()
    offers, gone = [], []
    try:
        for i, offer_id in enumerate(ids, 1):
            offer = ft.get(offer_id)
            if offer is None:
                gone.append(offer_id)
            else:
                offers.append(offer)
            if i % 250 == 0:
                print(f"  {i}/{len(ids)} offres relues", flush=True)
    except CredentialsError as exc:
        sys.exit(str(exc))
    _store(offers, "France Travail, offres relues")
    print(f"{db.close_offers(conn, 'france_travail', gone)} offres retirées : plus en ligne.")


def cmd_hiring(args) -> None:
    town = _town(args.town)
    romes = ROME_FIELDS.get(args.field) or [r.strip() for r in args.field.split(",")]
    lbb = LaBonneBoite()
    conn = _connect()
    try:
        for rome in romes:
            db.upsert_hiring(conn, lbb.search(rome, town.latitude, town.longitude, args.radius))
    except CredentialsError as exc:
        sys.exit(str(exc))
    rows = db.likely_employers(conn, town.latitude, town.longitude, args.radius, romes)
    print(
        f"{len(rows)} entreprises susceptibles de recruter ({', '.join(romes)}) "
        f"à moins de {args.radius:g} km de {town.name}, par potentiel d'embauche :\n"
    )
    for e in rows:
        flags = " ★ fort potentiel" if e.is_high_potential else ""
        flags += " · candidature spontanée par e-mail acceptée" if e.accepts_email else ""
        print(f"{e.score:6.1f}  {e.name[:45]:45} {e.distance_km:5.1f} km  {e.city}{flags}")
        print(f"{'':8}fiche : https://annuaire-entreprises.data.gouv.fr/etablissement/{e.siret}")


def cmd_index(args) -> None:
    from .embed import Embedder, offer_text

    conn = _connect()
    todo = db.offers_to_embed(conn)
    if not todo:
        print("Toutes les offres sont déjà indexées.")
        return
    embedder = Embedder()
    done = 0
    for start in range(0, len(todo), 64):
        batch = todo[start : start + 64]
        vectors = embedder.passages([offer_text(t, c, city, k, d) for _, t, c, city, k, d in batch])
        db.set_embeddings(conn, [(row[0], v) for row, v in zip(batch, vectors, strict=True)])
        done += len(batch)
        print(f"  {done}/{len(todo)} offres indexées")


def _hours(weekly_hours: float | None) -> str:
    return f", {weekly_hours:g} h/semaine" if weekly_hours else ""


def cmd_search(args) -> None:
    from pathlib import Path

    from .embed import Embedder
    from .pipeline import CONFIDENCE, find
    from .rerank import Reranker
    from .rewrite import rewrite

    profile = Path(args.profile).read_text(encoding="utf-8") if args.profile else ""
    rewritten = rewrite(args.request, profile) if args.rewrite else None
    town_name = args.town or (rewritten.town if rewritten else None)
    if not town_name:
        sys.exit("Indiquer une commune (--town), ou la nommer dans la demande.")
    town = _town(town_name)
    radius = args.radius or (rewritten.radius_km if rewritten and rewritten.radius_km else 30)
    results = find(
        _connect(),
        args.request,
        town.latitude,
        town.longitude,
        radius,
        Embedder(),
        kinds=[Kind(k) for k in args.kind or ()],
        profile=profile,
        rewriter=(lambda request, prof: rewritten) if rewritten else None,
        reranker=Reranker() if args.rerank else None,
        mode=args.mode,
        limit=args.n,
    )
    q = results.query
    print(f"Recherche : « {q.text} » autour de {town.name} ({radius:g} km)")
    if q.keywords:
        print(f"Mots-clés : {', '.join(q.keywords)}")
    if q.kinds:
        print(f"Contrats : {', '.join(KIND_LABELS[k.value] for k in q.kinds)}")
    print()
    if not results.confident:
        best = (
            f" (meilleur score {results.best_score:.2f} < {CONFIDENCE})"
            if results.best_score
            else ""
        )
        print(
            f"Pas assez d'offres pertinentes pour cette demande{best}. "
            "Élargir le rayon ou la demande.\n"
        )
    for i, c in enumerate(results.candidates, 1):
        score = f"{c.extra['rerank']:.2f}" if "rerank" in c.extra else f"{c.score:.3f}"
        what = ", ".join(KIND_LABELS[k] for k in c.kinds) + _hours(c.weekly_hours)
        print(f"{i:2}. [{score}] {c.title[:70]}  ({what}, {c.distance_km:.0f} km)")
        print(f"      {c.company or '?'} — {c.city} — {c.url}")


def _eval_setup(args):
    from pathlib import Path

    from .embed import Embedder
    from .evaluate import CONFIGS, CachedRewriter, load_queries
    from .rerank import Reranker
    from .rewrite import rewrite

    queries = load_queries(Path(args.queries))
    towns = {name: _town(name) for name in {q.town for q in queries}}
    profile = Path(args.profile).read_text(encoding="utf-8") if args.profile else ""
    rewriter = CachedRewriter(Path(args.rewrites), rewrite)
    configs = list(CONFIGS)
    try:
        for q in queries:
            rewriter(q.request, profile)
    except Exception as exc:  # no credit, no key, network: evaluate without rewriting
        print(f"Réécriture indisponible ({type(exc).__name__}) : configurations sans réécriture.")
        configs = [c for c in configs if not CONFIGS[c][1]]
    return queries, towns, profile, rewriter, configs, Embedder(), Reranker()


def cmd_eval_pool(args) -> None:
    import json
    from pathlib import Path

    from .evaluate import collect, pool, sheets

    queries, towns, profile, rewriter, configs, embedder, reranker = _eval_setup(args)
    rankings, items = collect(
        _connect(), queries, towns, embedder, reranker, rewriter, profile, configs, args.depth
    )
    to_judge = pool(rankings, items, args.depth)
    Path(args.out).write_text(json.dumps(to_judge, ensure_ascii=False, indent=1) + "\n", "utf-8")
    out = Path(args.sheets)
    out.mkdir(parents=True, exist_ok=True)
    for query_id, sheet in sheets(queries, to_judge).items():
        (out / f"{query_id}.json").write_text(json.dumps(sheet, ensure_ascii=False), "utf-8")
    per_query = {q.id: sum(i["query_id"] == q.id for i in to_judge) for q in queries}
    print(f"{len(to_judge)} offres à juger ({per_query}), écrites dans {args.out} et {out}/")


def cmd_eval_score(args) -> None:
    from pathlib import Path

    from .evaluate import collect, load_labels, score, to_markdown

    queries, towns, profile, rewriter, configs, embedder, reranker = _eval_setup(args)
    labels = load_labels(Path(args.labels))
    rankings, _ = collect(
        _connect(), queries, towns, embedder, reranker, rewriter, profile, configs
    )
    unjudged = {k for per_q in rankings.values() for r in per_q.values() for k in r[:10]} - set(
        labels
    )
    print(to_markdown(score(rankings, labels)))
    if unjudged:
        print(
            f"\n{len(unjudged)} offres du top 10 ne sont pas jugées : relancer « radar eval pool »."
        )


def cmd_eval_import(args) -> None:
    import json
    from collections import Counter
    from pathlib import Path

    from .evaluate import import_labels

    labels = import_labels(Path(args.directory))
    Path(args.out).write_text(json.dumps(labels, ensure_ascii=False, indent=1) + "\n", "utf-8")
    counts = Counter((v["by"], v["label"]) for v in labels.values())
    print(f"{len(labels)} jugements écrits dans {args.out} :")
    for (by, label), n in sorted(counts.items()):
        print(f"  {by:8} {label:6} {n}")


def cmd_offers(args) -> None:
    town = _town(args.town)
    kinds = [Kind(k) for k in args.kind] if args.kind else None
    rows = db.offers_within(_connect(), town.latitude, town.longitude, args.radius, kinds)
    print(f"{len(rows)} offres à moins de {args.radius} km de {town.name}.\n")
    for o in rows:
        approx = "≈" if o.precision == "town" else " "
        what = ", ".join(KIND_LABELS[k] for k in o.kinds) + _hours(o.weekly_hours)
        print(f"{approx}{o.distance_km:5.1f} km  [{what}] {o.title[:60]}")
        print(f"{'':10}{o.company or '?'} — {o.city} — {o.url}")
        if o.contact:
            print(f"{'':10}contact publié : {o.contact[:120]}")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="radar", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("companies", help="les employeurs autour d'une commune")
    p.add_argument("--town", required=True)
    p.add_argument("--radius", type=float, default=10, help="en km (50 au plus)")
    p.add_argument(
        "--sections", help="sections d'activité INSEE, ex. J (information-communication)"
    )
    p.add_argument("--naf", help="préfixes de code NAF à afficher, ex. 62,63")
    p.add_argument("--max", type=int, default=300, help="nombre d'entreprises à lire au plus")
    p.set_defaults(func=cmd_companies)

    p = sub.add_parser("careers", help="lit les offres du site carrières d'une entreprise")
    p.add_argument("url")
    p.add_argument("--max-pages", type=int, default=50)
    p.set_defaults(func=cmd_careers)

    p = sub.add_parser("feed", help="lit le flux public d'un logiciel de recrutement")
    p.add_argument("provider", choices=["greenhouse", "lever", "recruitee"])
    p.add_argument("identifier")
    p.add_argument("--region", default="", choices=["", "eu"])
    p.set_defaults(func=cmd_feed)

    p = sub.add_parser("francetravail", help="les offres France Travail autour d'une commune")
    p.add_argument("--town")
    p.add_argument(
        "--refresh", action="store_true", help="relit les offres connues (heures, retraits)"
    )
    p.add_argument("--radius", type=float, default=20)
    p.add_argument("--kind", nargs="+", choices=[k.value for k in Kind])
    p.add_argument("--keywords", default="", help="mots-clés, ex. data")
    p.add_argument("--max", type=int, default=1000, help="offres au plus par type")
    p.set_defaults(func=cmd_francetravail)

    p = sub.add_parser("hiring", help="les entreprises qui recrutent, même sans offre publiée")
    p.add_argument("--town", required=True)
    p.add_argument("--radius", type=float, default=30)
    p.add_argument(
        "--field",
        default="data",
        help=f"domaine ({', '.join(ROME_FIELDS)}) ou codes ROME séparés par des virgules",
    )
    p.set_defaults(func=cmd_hiring)

    sub.add_parser(
        "index", help="calcule les vecteurs des offres nouvelles ou modifiées"
    ).set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="recherche les offres qui correspondent à une demande")
    p.add_argument("request", help='ex. "alternance data"')
    p.add_argument("--town", help="commune (sinon celle nommée dans la demande)")
    p.add_argument("--radius", type=float, help="en km (défaut 30)")
    p.add_argument("--kind", nargs="+", choices=[k.value for k in Kind])
    p.add_argument(
        "--profile", default="data/profile.md" if os.path.exists("data/profile.md") else None
    )
    p.add_argument("--mode", choices=["hybrid", "vector", "fulltext"], default="hybrid")
    p.add_argument("--no-rewrite", dest="rewrite", action="store_false", help="sans Claude")
    p.add_argument("--no-rerank", dest="rerank", action="store_false")
    p.add_argument("-n", type=int, default=15, help="nombre de résultats")
    p.set_defaults(func=cmd_search)

    ev = sub.add_parser("eval", help="mesure la qualité de la recherche").add_subparsers(
        dest="eval_command", required=True
    )
    for name, func, help_ in (
        ("pool", cmd_eval_pool, "réunit les offres à juger"),
        ("score", cmd_eval_score, "calcule nDCG, MRR, précision et rappel par configuration"),
    ):
        p = ev.add_parser(name, help=help_)
        p.add_argument("--queries", default="eval/queries.json")
        p.add_argument("--rewrites", default="eval/rewrites.json")
        p.add_argument(
            "--profile", default="data/profile.md" if os.path.exists("data/profile.md") else None
        )
        p.set_defaults(func=func)
        if name == "pool":
            p.add_argument("--depth", type=int, default=10)
            p.add_argument("--out", default="eval/pool.json")
            p.add_argument("--sheets", default="eval/sheets", help="fiches à juger, en aveugle")
        else:
            p.add_argument("--labels", default="eval/labels.json")

    p = ev.add_parser("import", help="reprend les jugements exportés de la page d'étiquetage")
    p.add_argument("directory", help="un fichier JSON {label, by?, note?} par offre")
    p.add_argument("--out", default="eval/labels.json")
    p.set_defaults(func=cmd_eval_import)

    p = sub.add_parser("offers", help="le radar : les offres autour d'une commune")
    p.add_argument("--town", required=True)
    p.add_argument("--radius", type=float, default=20)
    p.add_argument("--kind", nargs="+", choices=[k.value for k in Kind])
    p.set_defaults(func=cmd_offers)

    args = parser.parse_args(argv)
    args.func(args)
