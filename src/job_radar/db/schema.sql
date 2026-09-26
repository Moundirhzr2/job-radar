-- Job Radar : schéma PostgreSQL (PostGIS + pgvector).
-- Les positions sont en geography(Point, 4326) : les distances se calculent en mètres sur le
-- globe, ce qui permet la question du radar : « tout ce qui est à moins de X km d'ici ».

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

-- Une entreprise (unité légale). Le SIREN vient du registre public quand il est connu.
CREATE TABLE IF NOT EXISTS companies (
    id               bigserial PRIMARY KEY,
    siren            text UNIQUE,
    name             text NOT NULL,
    naf_code         text,                 -- activité principale (nomenclature INSEE)
    naf_section      text,                 -- section d'activité (J = information et communication)
    headcount_range  text,                 -- tranche d'effectif salarié (code INSEE)
    category         text,                 -- PME, ETI, GE
    website          text,
    careers_url      text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Un établissement : un lieu physique d'une entreprise. C'est lui qui est placé sur la carte.
CREATE TABLE IF NOT EXISTS establishments (
    siret         text PRIMARY KEY,
    company_id    bigint NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    address       text,
    postal_code   text,
    city          text,
    location      geography(Point, 4326),
    opened_on     date,                    -- un établissement récent est un signal d'embauche
    is_head_office boolean NOT NULL DEFAULT false,
    headcount_range text
);
CREATE INDEX IF NOT EXISTS establishments_location ON establishments USING gist (location);

-- Les offres, toutes sources confondues, dans un format commun.
CREATE TABLE IF NOT EXISTS offers (
    id               bigserial PRIMARY KEY,
    source           text NOT NULL,        -- france_travail, careers:jsonld, ats:lever, ...
    source_id        text NOT NULL,
    url              text NOT NULL,        -- la page d'origine : on postule toujours à la source
    title            text NOT NULL,
    company_id       bigint REFERENCES companies (id) ON DELETE SET NULL,
    company_name     text NOT NULL DEFAULT '',
    description      text NOT NULL DEFAULT '',
    kinds            text[] NOT NULL,
    employment_types text[] NOT NULL DEFAULT '{}',
    city             text NOT NULL DEFAULT '',
    postal_code      text NOT NULL DEFAULT '',
    country          text NOT NULL DEFAULT '',
    location         geography(Point, 4326),
    remote           boolean,
    published_at     timestamptz,
    valid_through    timestamptz,
    first_seen       timestamptz NOT NULL DEFAULT now(),
    last_seen        timestamptz NOT NULL DEFAULT now(),  -- l'offre a disparu si last_seen stagne
    content_hash     text NOT NULL,
    embedding        vector(1024),
    UNIQUE (source, source_id),
    CHECK (kinds <@ ARRAY['internship', 'apprenticeship', 'student_job', 'job']
           AND cardinality(kinds) > 0)
);
CREATE INDEX IF NOT EXISTS offers_location ON offers USING gist (location);
CREATE INDEX IF NOT EXISTS offers_kinds ON offers USING gin (kinds);
CREATE INDEX IF NOT EXISTS offers_embedding ON offers USING hnsw (embedding vector_cosine_ops);

-- Les flux publics de logiciels de recrutement détectés sur les pages carrières.
CREATE TABLE IF NOT EXISTS ats_boards (
    id            bigserial PRIMARY KEY,
    company_id    bigint REFERENCES companies (id) ON DELETE CASCADE,
    provider      text NOT NULL,
    identifier    text NOT NULL,
    region        text NOT NULL DEFAULT '',
    last_checked  timestamptz,
    UNIQUE (provider, identifier, region)
);

-- Le chemin vers la source. Seulement ce que l'entreprise publie elle-même (offre, site) ou
-- ce qui est public par la loi (registre des entreprises) : jamais d'adresse personnelle
-- collectée ou devinée. source_url permet de toujours montrer d'où vient l'information.
CREATE TABLE IF NOT EXISTS company_contacts (
    id          bigserial PRIMARY KEY,
    company_id  bigint NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN (
                    'offer_contact',      -- contact publié dans une offre
                    'careers_page',       -- page carrières
                    'recruitment_email',  -- adresse de recrutement publiée par l'entreprise
                    'phone',              -- standard publié
                    'registry_officer'    -- dirigeant, registre public des entreprises
                )),
    label       text NOT NULL DEFAULT '',
    value       text NOT NULL,
    source_url  text NOT NULL,
    found_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, kind, value)
);
