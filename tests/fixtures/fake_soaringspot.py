"""A local stand-in for a SoaringSpot daily results page.

soaringspot.com cannot be reached from CI (or from the environment this was
written in), and a scraper tested only against a mock of its own assumptions
proves nothing. This serves a page shaped like the real one over HTTP so
``opensoar``'s actual scraping code runs end to end: it must find the table,
read the contest-number column, dig the IGC link out of the ``data-content``
popover attribute, and fetch each file.

The markup mirrors the parts opensoar depends on. If SoaringSpot changes those,
this fixture stops resembling reality — which is a limitation to know about, not
one this can fix.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests.fixtures.synthetic import DEFAULT_TASK, build_igc


@dataclass(frozen=True)
class Competitor:
    ranking: str
    competition_id: str
    pilot: str
    glider: str
    cruise_speed: float = 33.0
    climb_rate: float = 2.2


# Several classes, several days: enough shape to test discovering a whole
# competition from one link.
DEFAULT_DAYS: tuple[tuple[str, str], ...] = (
    ("club", "task-1-on-2024-06-15"),
    ("club", "task-2-on-2024-06-16"),
    ("18m", "task-1-on-2024-06-15"),
    ("18m", "task-3-on-2024-06-17"),
)


DEFAULT_COMPETITORS = (
    Competitor("1", "7L", "Anna Beispiel", "Ventus 3", 34.0, 2.4),
    Competitor("2", "XY", "Beat Muster", "ASG 29", 31.0, 2.0),
    Competitor("HC", "ZZ", "Carla Ospite", "LS8", 29.0, 1.8),
)


def competition_index_html(competition: str, days: tuple[tuple[str, str], ...], lang: str) -> str:
    """The competition's results page: links to every class and day.

    Real pages vary wildly in layout, so discovery reads anchors rather than
    tables — a link is a link whatever it is wrapped in.
    """
    links = "".join(
        f'<li><a href="/{lang}/{competition}/results/{klass}/{date}/daily">{klass} — {date}</a></li>'
        for klass, date in days
    )
    return (
        "<html><body><h1>Results</h1>"
        f'<nav><a href="/{lang}/{competition}/">Home</a>'
        f'<a href="/{lang}/{competition}/news">News</a></nav>'
        f"<ul>{links}</ul></body></html>"
    )


def results_html(competitors: tuple[Competitor, ...]) -> str:
    """The results table, with IGC links inside data-content popovers."""
    rows = []
    for competitor in competitors:
        # The download link is not a plain href: it is HTML embedded in the
        # data-content attribute of the anchor in the CN column.
        popover = f'<a href="/download/{competitor.competition_id}.igc">Download IGC</a>'
        escaped = (
            popover.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        )
        rows.append(
            "<tr>"
            f"<td>{competitor.ranking}</td>"
            "<td></td>"
            f'<td><a href="#" data-content="{escaped}">{competitor.competition_id}</a></td>'
            f'<td><div class="flag">IT</div>{competitor.pilot}</td>'
            f"<td>{competitor.glider}</td>"
            "<td>101.5</td>"
            "</tr>"
        )
    return (
        "<html><body><table>"
        "<thead><tr><th>#</th><th></th><th>CN</th>"
        "<th>Contestant</th><th>Glider</th><th>Speed</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table></body></html>"
    )


class _Handler(BaseHTTPRequestHandler):
    competitors: tuple[Competitor, ...] = DEFAULT_COMPETITORS
    date: dt.date = dt.date(2024, 6, 15)
    request_log: list[str] = field(default_factory=list)

    days: tuple[tuple[str, str], ...] = DEFAULT_DAYS

    def do_GET(self):
        type(self).request_log.append(self.path)

        parts = [p for p in self.path.split("/") if p]
        # /<lang>/<competition>/results  (and /results/<class>) list the days.
        if len(parts) in (3, 4) and parts[2] == "results":
            body = competition_index_html(parts[1], self.days, parts[0]).encode()
            if len(parts) == 4:  # a class page shows only that class
                body = competition_index_html(
                    parts[1], tuple(d for d in self.days if d[0] == parts[3]), parts[0]
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.endswith(".igc"):
            competition_id = self.path.rsplit("/", 1)[-1][: -len(".igc")]
            match = [c for c in self.competitors if c.competition_id == competition_id]
            if not match:
                self.send_error(404)
                return
            competitor = match[0]
            body = build_igc(
                task=DEFAULT_TASK,
                date=self.date,
                pilot=competitor.pilot,
                glider_model=competitor.glider,
                competition_id=competitor.competition_id,
                cruise_speed=competitor.cruise_speed,
                climb_rate=competitor.climb_rate,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
        else:
            body = results_html(self.competitors).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")

        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output clean
        pass


class FakeSoaringSpot:
    """Serves the page and its IGC files on a loopback port."""

    def __init__(
        self,
        competitors: tuple[Competitor, ...] = DEFAULT_COMPETITORS,
        date: dt.date = dt.date(2024, 6, 15),
        days: tuple[tuple[str, str], ...] = DEFAULT_DAYS,
    ):
        _Handler.competitors = competitors
        _Handler.date = date
        _Handler.days = days
        _Handler.request_log = []
        self.days = days
        self.date = date
        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> FakeSoaringSpot:
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def competition_url(self, competition: str = "test-comp", lang: str = "en") -> str:
        return f"http://127.0.0.1:{self.port}/{lang}/{competition}/results"

    def daily_url(self, competition: str = "test-comp", plane_class: str = "club") -> str:
        return (
            f"http://127.0.0.1:{self.port}/en/{competition}/results/"
            f"{plane_class}/task-1-on-{self.date:%Y-%m-%d}/daily"
        )

    @property
    def requests_made(self) -> list[str]:
        return list(_Handler.request_log)
