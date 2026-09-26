# PostgreSQL 17 avec PostGIS (recherches par distance) et pgvector (recherche sémantique),
# les deux extensions disponibles sur Supabase, où l'application sera hébergée.
FROM postgis/postgis:17-3.5
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-pgvector \
 && rm -rf /var/lib/apt/lists/*
