# debrief

Debrief sailplane competition flights: analyse a flight against the task it was
flown on, and — later — compare it against other pilots and your own past flights.

Built on [`aerofiles`](https://github.com/Turbo87/aerofiles) for IGC parsing and
[`opensoar`](https://github.com/glidergeek/opensoar) for task reconstruction, trip
scoring and thermal detection.

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"

# Generate a demo flight (a synthetic 311 km triangle) to try it on
.venv/bin/python tests/fixtures/synthetic.py data/igc/demo.igc

# Command line
.venv/bin/python -m debrief.cli data/igc/demo.igc

# Web app
.venv/bin/streamlit run src/debrief/app/main.py
```

Drop your own `.igc` files into `data/igc/` (any subdirectory layout works), or
upload one in the app's sidebar.

## What it measures

Everything comes from the IGC file alone. Competition files from SoaringSpot carry
the task declaration in their `LCU::C` / `LSEEYOU OZ` comment lines, so no separate
task download is needed — each file is self-describing.

Four metric families, per leg and for the whole flight:

| Family | Metrics |
|---|---|
| **Climb** | thermal count, average climb rate, time and share spent circling |
| **Cruise & glide** | inter-thermal speed, achieved L/D, cruise detour |
| **Task execution** | per-leg speed, start time and height, final glide |
| **Energy** | working altitude band per leg, height gained and lost |

**On "detour":** it measures cruise track against cruise straight-line distance —
how straight you flew *between* thermals. Comparing total track to task distance
instead would fold circling into the number (a normal flight lands near +50%, which
says nothing about course-keeping) and would be distorted by thermal drift, which
carries you along the task for free.

## Layout

```
src/debrief/
  core/       pure analysis — no I/O, no UI
    models.py   Flight (fact) + Pilot / TaskDef (dimensions)
    igc.py      the only module that knows IGC syntax
    metrics.py  the four metric families
  sources/    importers; one per place flights come from
  app/        Streamlit UI, Plotly charts, pydeck map
  cli.py      text debrief
tests/
  fixtures/synthetic.py   generates a flown task as a valid IGC file
```

`core` has no dependency on `app`. The same `analyse()` call backs the CLI today
and a web API later.

### Comparison model

`Flight` is a fact carrying both a `pilot_key` and a `task_key`; `Pilot` and
`TaskDef` are peer dimensions. A task's key is derived from turnpoint geometry, so
every pilot on a competition day resolves to the *same* task even though each IGC
file carries its own copy of the declaration. That is what lets flights be grouped
either way — same task across pilots, or one pilot across days — without either
axis being privileged.

## Data sources

Currently: **local files you downloaded yourself.** `sources/base.py` is the seam
for the rest.

- **SoaringSpot scraping** — `opensoar.competition.soaringspot.SoaringSpotDaily`
  takes a daily-results URL, finds each competitor's IGC link (it lives inside the
  `data-content` popover attribute on the CN column, not a plain `href`) and
  downloads the lot. No authentication.
- **SoaringSpot API** — REST, hal+json, HMAC-signed. The catch is that the
  AppID/secret is issued *per competition* by that competition's organiser, so it
  cannot cover contests you have no contact at.

Either way, treat the raw IGC file as the durable asset: fetch once, store it with
its `source_url` and `fetched_at`, derive everything else locally. Page markup
changes; your archive shouldn't have to.

## Base maps

Terrain matters more than roads for soaring, so the default is **OpenTopoMap**
rather than standard OSM carto — which is also the tile service the OSM Foundation
asks applications not to consume. **swisstopo** is selectable and is much better in
the Alps: no API key, access granted by Referer, free on localhost (a public
deployment needs a WMTS account).

## Known constraints

- `opensoar` 2.1.3 pins `aerofiles<1.5`, so `aerofiles` 1.5.x on PyPI cannot be
  used with it. Pinned accordingly in `pyproject.toml`.
- Multistart tasks are not scoreable — `opensoar` does not support them.
- AAT scoring works but is far less exercised here than race tasks.

## Testing

```bash
.venv/bin/python -m pytest
```

There are no real competition IGC files in the repo. `tests/fixtures/synthetic.py`
flies a synthetic glider around a 311 km triangle and writes a valid SoaringSpot
-flavoured IGC file. Because that glider has a *known* polar — 33 m/s cruise at
1.15 m/s sink, so L/D 28.7 — the fixture doubles as ground truth: the metric tests
assert the analysis recovers those numbers.

The thermals in the fixture are flown as real circles rather than by incrementing
altitude, because the PySoar detector triggers on >225° of consistent turn. A
fixture that simply climbed would parse cleanly and silently yield zero thermals.
