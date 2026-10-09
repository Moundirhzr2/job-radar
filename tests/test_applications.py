"""The application tracker: what is copied from the offer, and the dates it plans."""

from datetime import date, timedelta

import pytest

from job_radar import db
from tests.test_db import offer


def test_tracking_an_offer_copies_what_it_says(conn):
    db.upsert_offers(conn, [offer("a", "Mulhouse", company="ACME", contact="Mme Durand")])
    (offer_id,) = conn.execute("SELECT id FROM offers").fetchone()
    a = db.add_application(conn, offer_id=offer_id)
    assert (a.title, a.company, a.contact, a.status) == (
        "Offre a",
        "ACME",
        "Mme Durand",
        "to_apply",
    )
    assert a.url == "https://example.org/a" and a.offer_open is True
    assert a.applied_on is None and a.follow_up_on is None

    db.close_offers(conn, "test", ["a"])
    a = db.get_application(conn, a.id)
    assert a.offer_open is False and a.title == "Offre a"  # still readable once withdrawn


def test_sending_dates_the_application_and_plans_the_follow_up(conn):
    a = db.add_application(conn, siret="12345678900011", company="Boulangerie", channel="sur place")
    assert a.offer_open is None  # a spontaneous application, no offer behind it
    a = db.update_application(conn, a.id, status="sent")
    assert a.applied_on == date.today()
    assert a.follow_up_on == date.today() + timedelta(days=db.FOLLOW_UP_DAYS)

    a = db.update_application(conn, a.id, follow_up_on=date.today() - timedelta(days=1))
    a = db.update_application(conn, a.id, status="followed_up")  # followed up today
    assert a.follow_up_on == date.today() + timedelta(days=db.FOLLOW_UP_DAYS)

    a = db.update_application(conn, a.id, status="interview", notes="Entretien mardi")
    assert a.follow_up_on is None and a.notes == "Entretien mardi"  # an answer came


def test_dates_set_by_the_student_are_kept(conn):
    sent = date(2026, 10, 1)
    a = db.add_application(conn, company="ACME", status="sent", applied_on=sent)
    assert a.follow_up_on == sent + timedelta(days=7)
    a = db.update_application(conn, a.id, follow_up_on=date(2026, 10, 20))
    assert a.follow_up_on == date(2026, 10, 20)


def test_only_the_application_fields_change(conn):
    a = db.add_application(conn, company="ACME")
    with pytest.raises(ValueError):
        db.update_application(conn, a.id, offer_id=1)
    with pytest.raises(ValueError):
        db.add_application(conn, company="ACME", salary=40000)
    with pytest.raises(ValueError, match="unknown offer"):
        db.add_application(conn, offer_id=999999)
    assert db.update_application(conn, 999999, notes="x") is None


def test_listing_puts_the_next_follow_up_first_and_delete(conn):
    late = db.add_application(conn, company="B", status="sent", applied_on=date(2026, 9, 1))
    soon = db.add_application(conn, company="C", status="sent", applied_on=date(2026, 9, 20))
    idea = db.add_application(conn, company="A")
    assert [a.id for a in db.list_applications(conn)] == [late.id, soon.id, idea.id]
    assert db.delete_application(conn, idea.id) and not db.delete_application(conn, idea.id)
