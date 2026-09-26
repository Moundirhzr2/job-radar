# Job Radar

**Moins de candidatures, envoyées aux bonnes personnes.**

Job Radar montre aux étudiants ce qui recrute autour d'eux (stages, alternances, emplois, jobs
étudiants), y compris les entreprises qui n'ont rien publié, et le chemin le plus direct vers la
personne qui décide. Uniquement à partir de sources officielles et de données que les
entreprises publient elles-mêmes.

Projet en construction : le produit, les règles et l'avancement sont décrits dans
[PLAN.md](PLAN.md).

## Développement

```bash
docker compose up -d --wait   # PostgreSQL 17 + PostGIS + pgvector
uv sync
uv run pytest
```
