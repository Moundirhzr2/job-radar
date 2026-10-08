# PostgreSQL 17 avec pgvector (recherche sémantique) et PostGIS (recherches par distance),
# les deux extensions disponibles sur Supabase, où l'application sera hébergée.
# Base : l'image officielle pgvector sur Debian 12. L'image postgis/postgis:17-3.5 repose sur
# Debian 11, que le dépôt apt.postgresql.org ne sert plus.
FROM pgvector/pgvector:0.8.7-pg17-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-postgis-3 \
 && rm -rf /var/lib/apt/lists/*
