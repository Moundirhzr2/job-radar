"""Links to the same search on the big job sites: words, town, radius and contracts filled in."""

from urllib.parse import parse_qs, urlsplit

from job_radar.elsewhere import links
from job_radar.models import Kind

SITES = ["LinkedIn", "Welcome to the Jungle", "HelloWork", "Indeed"]
CONTRACT = "refinementList[contract_type][]"  # Welcome to the Jungle


def params(found, site):
    (url,) = [link.url for link in found if link.site == site]
    return parse_qs(urlsplit(url).query)


def test_one_link_per_site_with_the_town_and_a_radius_that_covers_the_circle():
    found = links("data analyst", "Mulhouse", 30)
    assert [link.site for link in found] == SITES
    linkedin = params(found, "LinkedIn")
    assert linkedin["keywords"] == ["data analyst"]
    assert linkedin["location"] == ["Mulhouse, France"]
    assert linkedin["distance"] == ["25"]  # 30 km = 18.6 miles, LinkedIn offers 10 or 25
    assert "f_JT" not in linkedin  # no contract chosen: all of them
    assert params(found, "Indeed")["radius"] == ["35"]
    assert params(found, "HelloWork") == {"k": ["data analyst"], "l": ["Mulhouse"], "ray": ["30"]}
    wttj = {"query": ["data analyst"], "aroundQuery": ["Mulhouse"]}
    assert params(found, "Welcome to the Jungle") == wttj


def test_a_radius_beyond_what_a_site_offers_takes_its_largest():
    found = links("data", "Mulhouse", 200)
    assert params(found, "LinkedIn")["distance"] == ["100"]
    assert params(found, "Indeed")["radius"] == ["100"]


def test_an_internship_is_filtered_where_the_site_can_and_written_where_it_cannot():
    found = links("data", "Mulhouse", 30, [Kind.INTERNSHIP])
    assert params(found, "LinkedIn")["f_JT"] == ["I"]
    assert params(found, "LinkedIn")["keywords"] == ["data"]
    assert params(found, "Welcome to the Jungle")[CONTRACT] == ["internship"]
    assert params(found, "HelloWork")["k"] == ["data stage"]
    assert params(found, "Indeed")["q"] == ["data stage"]


def test_linkedin_has_no_apprenticeship_filter_so_the_word_goes_into_the_search():
    found = links("data", "Mulhouse", 30, [Kind.APPRENTICESHIP])
    assert "f_JT" not in params(found, "LinkedIn")
    assert params(found, "LinkedIn")["keywords"] == ["data alternance"]
    assert params(found, "Welcome to the Jungle")[CONTRACT] == ["apprenticeship"]


def test_a_kind_a_site_cannot_filter_lifts_its_filter_rather_than_hide_that_kind():
    found = links("data", "Mulhouse", 30, [Kind.INTERNSHIP, Kind.APPRENTICESHIP])
    linkedin = params(found, "LinkedIn")
    assert "f_JT" not in linkedin and linkedin["keywords"] == ["data"]
    wttj = params(found, "Welcome to the Jungle")
    assert wttj[CONTRACT] == ["internship", "apprenticeship"]
    assert params(found, "Indeed")["q"] == ["data"]  # two kinds: no single word to add


def test_the_contract_word_is_not_repeated():
    found = links("alternance data", "Mulhouse", 30, [Kind.APPRENTICESHIP])
    assert params(found, "LinkedIn")["keywords"] == ["alternance data"]
    assert params(found, "Indeed")["q"] == ["alternance data"]


def test_jobs_and_student_jobs_on_linkedin():
    found = links("vendeur", "Colmar", 10, [Kind.JOB, Kind.STUDENT_JOB])
    assert params(found, "LinkedIn")["f_JT"] == ["F,C,T,P"]
    assert params(found, "LinkedIn")["distance"] == ["10"]
    assert params(found, "Welcome to the Jungle")[CONTRACT] == [
        "full_time", "temporary", "part_time",
    ]  # fmt: skip


def test_without_a_radius_every_site_searches_all_of_france():
    found = links("data", "Mulhouse", None, [Kind.INTERNSHIP])
    linkedin = params(found, "LinkedIn")
    assert linkedin["location"] == ["France"] and "distance" not in linkedin
    assert params(found, "Welcome to the Jungle")["aroundQuery"] == ["France"]
    assert params(found, "HelloWork") == {"k": ["data stage"]}
    assert params(found, "Indeed") == {"q": ["data stage"]}
