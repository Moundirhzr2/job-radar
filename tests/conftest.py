import os

import psycopg
import pytest

from job_radar import db

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://radar:radar@localhost:5433/radar_test"
)


@pytest.fixture
def conn():
    """A clean database with the schema, or skip when PostgreSQL is not running."""
    base, _, name = TEST_DATABASE_URL.rpartition("/")
    try:
        with psycopg.connect(f"{base}/postgres", autocommit=True, connect_timeout=3) as admin:
            exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            if exists.fetchone() is None:
                admin.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as exc:
        if os.environ.get("REQUIRE_DB"):  # set in CI, where a missing database is a failure
            raise
        pytest.skip(f"PostgreSQL indisponible : {exc}")
    with db.connect(TEST_DATABASE_URL) as c:
        c.execute(
            "DROP TABLE IF EXISTS offer_fits, hiring_potential, company_contacts, ats_boards, "
            "offers, establishments, companies"
        )
        c.commit()
        db.init_schema(c)
        yield c
