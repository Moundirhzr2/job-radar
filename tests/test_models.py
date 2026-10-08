"""Offer kinds and working hours, on wordings taken from real offers."""

import pytest

from job_radar.models import Kind, classify, weekly_hours


@pytest.mark.parametrize(
    "text, hours",
    [
        ("Temps partiel - 24H/semaine\nTravail en journée", 24),
        ("Temps partiel - 34H12/semaine", 34.2),
        ("CDI Temps partiel (24h Hebdo)", 24),
        ("contrat de 24 heures hebdomadaires", 24),
        ("à raison de 17H30 par semaine", 17.5),
        # a range counts by its lowest value: the student can ask for it
        ("un temps partiel de 20 à 25 heures par semaine", 20),
        ("Possibilité de contrats de 24 heures à 30 heures par semaine", 24),
        ("CDI de 12 à 30 heures semaine", 12),
        ("choisissez votre volume horaire hebdomadaire, de 4 à 24 heures", 4),
        ("Temps de travail : 74h par mois", 17.08),
        # opening times, daily hours and rates are not a contract's weekly hours
        ("ouvert du mardi au samedi de 17h30 à 0h30", None),
        ("Horaires : 8h-15h", None),
        ("Temps de travail effectif : 7 heures par jour", None),
        ("TH à 12.04EUR/H", None),
        ("151,67 h par mois", None),
        ("2 heures par semaine de formation", None),
    ],
)
def test_weekly_hours(text, hours):
    assert weekly_hours(text) == hours


def test_lowest_hours_across_texts():
    assert (
        weekly_hours("Temps partiel - 30H/semaine", "contrats de 24 à 30 heures par semaine") == 24
    )
    assert weekly_hours(None, "") is None


@pytest.mark.parametrize(
    "title, part_time, hours, student",
    [
        ("Equipier polyvalent", False, 24, True),
        ("Employé polyvalent", True, 26, True),  # 26 h: still alongside classes
        ("Agent commercial", True, 34.2, False),  # "temps partiel" at 34 h 12 is not
        ("Employé commercial étudiant été", False, 35, False),  # full time, even for students
        ("Serveur", True, None, True),  # no hours published: "temps partiel" decides
        ("Job étudiant caisse", False, None, True),
        ("Serveur", False, None, False),
    ],
)
def test_student_job_follows_the_hours(title, part_time, hours, student):
    kinds = classify(title, [], part_time=part_time, weekly_hours=hours)
    assert Kind.JOB in kinds
    assert (Kind.STUDENT_JOB in kinds) is student


def test_apprenticeship_is_never_a_student_job():
    kinds = classify("Alternance développeur", [], part_time=True, weekly_hours=20)
    assert kinds == {Kind.APPRENTICESHIP}
