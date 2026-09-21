"""SoaringSpot day import.

These run opensoar's real scraping code against a local server serving a page
shaped like SoaringSpot's, rather than stubbing the scraper out. Stubbing would
only prove the mock matches my assumptions; this proves the integration finds
the table, digs the IGC link out of the data-content popover, and fetches the
files.

What it cannot prove is that SoaringSpot's markup still looks like this. That
is the standing risk of scraping, and the reason the downloaded IGC files are
the durable asset rather than anything re-derived from the page.
"""

import datetime as dt

import pytest

from debrief.core.metrics import analyse_or_summarise
from debrief.sources.soaringspot import (
    SoaringSpotDay,
    SoaringSpotError,
    normalise_daily_url,
    parse_daily_url,
)
from tests.fixtures.fake_soaringspot import DEFAULT_COMPETITORS, FakeSoaringSpot


@pytest.fixture
def server():
    with FakeSoaringSpot() as running:
        yield running


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.soaringspot.com/en/rieti-2026/results/club/task-1-on-2026-08-09/daily",
            ("rieti-2026", "club", dt.date(2026, 8, 9)),
        ),
        (
            "www.soaringspot.com/en/wgc-2025/results/18m/day-3-2025-07-14",
            ("wgc-2025", "18m", dt.date(2025, 7, 14)),
        ),
    ],
)
def test_daily_url_parsing(url, expected):
    assert parse_daily_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.soaringspot.com/en/rieti-2026/",
        "https://www.soaringspot.com/en/rieti-2026/news/something/else",
        "https://example.com/",
    ],
)
def test_a_url_that_is_not_a_daily_results_page_is_refused_early(url):
    """opensoar splits the URL positionally, so a bad one fails deep inside the
    download with an unhelpful error. Catch it where it can be explained."""
    with pytest.raises(SoaringSpotError, match="daily results URL"):
        parse_daily_url(url)


def test_url_without_a_readable_date_is_refused():
    with pytest.raises(SoaringSpotError, match="date"):
        parse_daily_url("https://www.soaringspot.com/en/comp/results/club/no-date-here/daily")


def test_imports_every_competitor(server, tmp_path):
    flights = list(SoaringSpotDay(server.daily_url(), tmp_path).flights())
    assert len(flights) == len(DEFAULT_COMPETITORS)
    assert {f.pilot.competition_id for f in flights} == {c.competition_id for c in DEFAULT_COMPETITORS}


def test_files_land_in_the_archive_and_are_reused(server, tmp_path):
    """The raw file is the durable asset: a second pass must not refetch."""
    day = SoaringSpotDay(server.daily_url(), tmp_path)
    list(day.flights())
    first_pass = len(server.requests_made)
    assert list(day.archive_paths()), "nothing was written to the archive"

    list(SoaringSpotDay(server.daily_url(), tmp_path).flights())
    assert len(server.requests_made) == first_pass, "second pass refetched the day"


def test_every_flight_carries_the_task_and_the_same_task_key(server, tmp_path):
    """The premise of same-task comparison, end to end from a results page."""
    flights = list(SoaringSpotDay(server.daily_url(), tmp_path).flights())
    assert all(f.task is not None for f in flights)
    assert len({f.task_key for f in flights}) == 1
    assert len({f.pilot_key for f in flights}) == len(flights)


def test_provenance_records_the_page_not_the_local_copy(server, tmp_path):
    flights = list(SoaringSpotDay(server.daily_url(), tmp_path).flights())
    for flight in flights:
        assert flight.source.kind == "soaringspot"
        assert flight.source.reference == server.daily_url()
        assert flight.competition.competition == "test-comp"
        assert flight.competition.plane_class == "club"


def test_imported_flights_analyse_and_rank_sensibly(server, tmp_path):
    """The faster simulated glider must score the faster task speed."""
    flights = {
        f.pilot.competition_id: analyse_or_summarise(f)
        for f in SoaringSpotDay(server.daily_url(), tmp_path).flights()
    }
    assert flights["7L"].task_speed_kmh > flights["XY"].task_speed_kmh
    assert flights["XY"].task_speed_kmh > flights["ZZ"].task_speed_kmh
    assert all(m.completed for m in flights.values())


def test_hors_concours_competitors_can_be_excluded(server, tmp_path):
    included = list(SoaringSpotDay(server.daily_url(), tmp_path / "with").flights())
    excluded = list(
        SoaringSpotDay(server.daily_url(), tmp_path / "without", include_hc_competitors=False).flights()
    )
    assert "ZZ" in {f.pilot.competition_id for f in included}
    assert "ZZ" not in {f.pilot.competition_id for f in excluded}


def test_an_unreachable_page_fails_with_a_usable_message(tmp_path):
    url = "http://127.0.0.1:9/en/comp/results/club/task-2024-06-15/daily"
    with pytest.raises(SoaringSpotError, match="could not import"):
        SoaringSpotDay(url, tmp_path).download()


def test_download_returns_a_sized_sequence(server, tmp_path):
    """Annotated as list[Path] but returned as an iterator, which every test
    here hid by wrapping it in list(). The app called len() on it and crashed."""
    paths = SoaringSpotDay(server.daily_url(), tmp_path).download()
    assert len(paths) == len(DEFAULT_COMPETITORS)
    assert all(p.suffix == ".igc" for p in paths)
    # Sequences can be walked twice; iterators cannot.
    assert list(paths) == list(paths)


# A real URL, as copied from the browser: a "en_gb" language segment and the
# "total" tab, which is the cumulative standings rather than the day's flights.
REAL_URL = (
    "https://www.soaringspot.com/en_gb/"
    "cim-coppa-internazionale-del-mediterraneo-rieti-2026/results/"
    "double-seater/task-10-on-2026-08-15/total"
)


def test_a_real_url_parses_including_its_language_segment():
    """en_gb rather than en, and a long competition slug."""
    competition, plane_class, date = parse_daily_url(REAL_URL)
    assert competition == "cim-coppa-internazionale-del-mediterraneo-rieti-2026"
    assert plane_class == "double-seater"
    assert date == dt.date(2026, 8, 15)


def test_the_total_tab_is_switched_to_daily():
    """Only the daily page carries per-competitor IGC links; scraping the
    totals page would simply find none."""
    url, note = normalise_daily_url(REAL_URL)
    assert url.endswith("/task-10-on-2026-08-15/daily")
    assert "total" in note and "daily" in note
    # Everything before the tab is preserved.
    assert "cim-coppa-internazionale-del-mediterraneo-rieti-2026" in url
    assert "/en_gb/" in url


def test_a_url_with_no_tab_gets_the_daily_one():
    url, note = normalise_daily_url("https://www.soaringspot.com/en/comp/results/club/task-1-on-2026-08-15")
    assert url.endswith("/daily")
    assert note


def test_an_already_daily_url_is_left_alone():
    url = "https://www.soaringspot.com/en/comp/results/club/task-1-on-2026-08-15/daily"
    assert normalise_daily_url(url) == (url, None)


def test_the_day_reports_the_switch(tmp_path):
    day = SoaringSpotDay(REAL_URL, tmp_path)
    assert day.url.endswith("/daily")
    assert day.url_note
    assert day.competition == "cim-coppa-internazionale-del-mediterraneo-rieti-2026"
    assert day.date == dt.date(2026, 8, 15)


def test_a_non_results_url_is_returned_untouched_for_the_parser_to_reject():
    url = "https://example.com/somewhere"
    assert normalise_daily_url(url) == (url, None)
    with pytest.raises(SoaringSpotError):
        parse_daily_url(url)


# -- whole-competition import -------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected_tail"),
    [
        (REAL_URL, "/en_gb/cim-coppa-internazionale-del-mediterraneo-rieti-2026/results"),
        ("https://www.soaringspot.com/en/comp/results/club/task-1-on-2024-06-15/daily", "/en/comp/results"),
        ("https://www.soaringspot.com/en/comp/results", "/en/comp/results"),
        ("https://www.soaringspot.com/en/comp/", "/en/comp/results"),
    ],
)
def test_the_competition_index_is_derived_from_any_url_inside_it(url, expected_tail):
    """Pasting one link should be enough; the index is derived, not asked for."""
    from debrief.sources.soaringspot import competition_results_url

    assert competition_results_url(url).endswith(expected_tail)


def test_discovery_finds_every_class_and_day(server, tmp_path):
    from debrief.sources.soaringspot import discover_days
    from tests.fixtures.fake_soaringspot import DEFAULT_DAYS

    days = discover_days(server.daily_url())
    assert {(d.plane_class, f"{d.date:%Y-%m-%d}") for d in days} == {
        (klass, date[-10:]) for klass, date in DEFAULT_DAYS
    }
    # Days are returned in flying order.
    assert [d.date for d in days] == sorted(d.date for d in days)


def test_discovery_ignores_links_that_are_not_days(server):
    """Most of a results page is navigation; only day links count."""
    from debrief.sources.soaringspot import discover_days

    days = discover_days(server.daily_url())
    assert all("/results/" in d.url for d in days)
    assert all(d.url.endswith("/daily") for d in days)
    assert all(d.competition == "test-comp" for d in days)


def test_class_import_takes_every_day_of_that_class_only(server, tmp_path):
    """A pilot flies one class; the others are a download nobody asked for."""
    from debrief.sources.local import archived_days
    from debrief.sources.soaringspot import import_class
    from tests.fixtures.fake_soaringspot import DEFAULT_DAYS

    expected = [d for d in DEFAULT_DAYS if d[0] == "club"]
    imported = import_class(server.daily_url(plane_class="club"), tmp_path)
    assert len(imported) == len(expected)

    archived = archived_days(tmp_path)
    assert {d.plane_class for d in archived} == {"club"}
    assert {f"{d.date:%Y-%m-%d}" for d in archived} == {date[-10:] for _, date in expected}


def test_class_import_follows_the_class_in_the_url(server, tmp_path):
    from debrief.sources.local import archived_days
    from debrief.sources.soaringspot import import_class

    import_class(server.daily_url(plane_class="18m"), tmp_path)
    assert {d.plane_class for d in archived_days(tmp_path)} == {"18m"}


def test_a_class_with_no_days_is_reported_not_silently_empty(server, tmp_path):
    from debrief.sources.soaringspot import import_days

    with pytest.raises(SoaringSpotError, match="no days found for class"):
        import_days(server.daily_url(), tmp_path, plane_class="standard")


def test_whole_competition_import_downloads_every_day(server, tmp_path):
    from debrief.sources.local import archived_days
    from debrief.sources.soaringspot import import_competition
    from tests.fixtures.fake_soaringspot import DEFAULT_DAYS

    imported = import_competition(server.daily_url(), tmp_path)
    assert len(imported) == len(DEFAULT_DAYS)

    archived = archived_days(tmp_path)
    assert len(archived) == len(DEFAULT_DAYS)
    assert {d.plane_class for d in archived} == {klass for klass, _ in DEFAULT_DAYS}
    assert all(len(d.paths) == len(DEFAULT_COMPETITORS) for d in archived)


def test_whole_competition_import_reports_progress(server, tmp_path):
    from debrief.sources.soaringspot import import_competition

    seen = []
    import_competition(server.daily_url(), tmp_path, progress=lambda i, n, label: seen.append((i, n, label)))
    assert seen[0][0] == 0
    assert seen[-1][0] == seen[-1][1], "progress must finish at 100%"
    assert all(total == seen[0][1] for _, total, _ in seen)


def test_a_second_whole_import_refetches_nothing(server, tmp_path):
    """Days already on disk are the durable asset; re-running resumes."""
    from debrief.sources.soaringspot import import_competition

    import_competition(server.daily_url(), tmp_path)
    igc_requests = [p for p in server.requests_made if p.endswith(".igc")]

    import_competition(server.daily_url(), tmp_path)
    assert [p for p in server.requests_made if p.endswith(".igc")] == igc_requests


def test_a_competition_with_no_day_links_says_so(tmp_path):
    from debrief.sources.soaringspot import import_competition

    url = "http://127.0.0.1:9/en/comp/results/club/task-1-on-2024-06-15/daily"
    with pytest.raises(SoaringSpotError, match=r"could not read|no competition days"):
        import_competition(url, tmp_path)
