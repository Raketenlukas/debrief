# debrief

Debrief sailplane competition flights: analyse a flight against the task it was
flown on, and — later — compare it against other pilots and your own past flights.

Built on [`aerofiles`](https://github.com/Turbo87/aerofiles) for IGC parsing and
[`opensoar`](https://github.com/glidergeek/opensoar) for task reconstruction, trip
scoring and thermal detection.

## Quick start

Needs **Python 3.11 or newer**. Check first — macOS still ships 3.9 as
`python3`, which is too old:

```bash
python3 -V
```

If you already have a `.venv` and are not sure what is in it:

```bash
.venv/bin/python -V            # 3.11+
.venv/bin/python -m pip -V     # 21.3+, or the editable install fails
```

Then, using only the standard library:

```bash
python3 -m venv .venv
# Required, not hygiene: this project builds with hatchling, and an editable
# install of a non-setuptools backend needs pip >= 21.3 (PEP 660). The pip that
# macOS seeds into a new venv is older and fails with "Directory cannot be
# installed in editable mode".
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"

# Generate a demo flight (a synthetic 311 km triangle) to try it on
.venv/bin/python tests/fixtures/synthetic.py data/igc/demo.igc

# Command line
.venv/bin/python -m debrief.cli data/igc/demo.igc

# Web app -> http://127.0.0.1:8501
.venv/bin/streamlit run src/debrief/app/main.py
```

If `python3 -V` is older than 3.11, or you would rather not manage Python
versions yourself, [uv](https://docs.astral.sh/uv/) downloads a suitable one
for you and installs considerably faster:

```bash
brew install uv          # or: curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.12 && uv pip install -e ".[dev]"
```

Either way the virtualenv lands in `.venv/` and the commands above are the
same. There is no need to `activate` it — every command here calls the
interpreter inside `.venv/` directly.

Drop your own `.igc` files into `data/igc/` (any subdirectory layout works), or
upload one in the app's sidebar.

## What it measures

Everything comes from the IGC file alone.

The task is read from whichever declaration the file carries. SoaringSpot and
SeeYou write `LCU::C` / `LSEEYOU OZ` comment lines, which include the observation
zones; those are preferred. Failing that, the standard IGC `C` records are used —
that is how most loggers declare a task, and reading only the comment lines made
ordinary competition files look like they had no task at all.

`C` records carry coordinates and names but **no sector geometry**, so such a task
is marked `geometry_assumed` and every view says so. Sector size decides when a
turnpoint counts as rounded, so an assumed sector smaller than the declared one is
never entered and a completed task reads as an outlanding. When that happens the
zones are widened and retried, and the analysis reports which sizes it had to use.

A flight with **no task at all** still opens. Thermals, climb rates, circling
share, cruise speed, achieved glide and the altitude band do not depend on a task;
only the task-shaped numbers are absent, and they are absent rather than zeroed.
Use `analyse_or_summarise()` for "just show me this flight".

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

- **SoaringSpot import** — built, in `sources/soaringspot.py`. Paste a results
  URL for **your class** and take every day of it, or just the single day:

  ```
  https://www.soaringspot.com/en_gb/<competition>/results/<class>/<date>/daily
  ```

  The class is taken from the URL and the competition index derived from it, so
  one link is enough. Other classes are left alone — a pilot flies one class,
  and the rest is a download nobody asked for. `import_competition()` takes the
  whole contest if you ever want it.

  Discovery reads **anchors, not tables**: results tables vary by
  competition and change between seasons, but a link to a day is a link
  whatever markup wraps it, which makes this the sturdier half of the scraping.

  Scraping is delegated to `opensoar`, which knows the awkward part — the IGC link
  is not a plain `href` but lives inside the `data-content` popover attribute on
  the contest-number column. No authentication. Files land in
  `data/igc/<comp>/<class>/<date>/` and are skipped on a second run.

  Because every downloaded file carries the task, one import gives you the whole
  day against a single `task_key` — which is the same-task comparison axis the
  data model was built for.
- **SoaringSpot API** — REST, hal+json, HMAC-signed. The catch is that the
  AppID/secret is issued *per competition* by that competition's organiser, so it
  cannot cover contests you have no contact at.

Either way, treat the raw IGC file as the durable asset: fetch once, store it with
its `source_url` and `fetched_at`, derive everything else locally. Page markup
changes; your archive shouldn't have to.

## Comparing a day

Switch the sidebar to **Compare a day** to put a field side by side over one
task. Pick the competition, class and day from the archive, then choose up to
eight pilots — eight because that is how many slots the palette has that clear
the colour-blind separation gates; past that pilots would share a colour and the
legend would stop meaning anything.

The barogram aligns two ways, and the difference matters:

| Align on | Answers |
|---|---|
| each pilot's own start | how was I doing at this point of *my* task — comparable even when starts were twenty minutes apart |
| absolute clock time | what was the sky doing *when* — whether a climb was there for everyone |

**Minutes lost per leg** measures each pilot against a reference you choose.
Bars rather than a running total, because the question is which leg cost the
time and a cumulative line hides a leg that was clawed back.

The comparison table covers four families: task speed and time delta, start
tactics (when and how high, and how long before the first climb), climb quality
(average, best, circling share, working band) and cruise efficiency (speed,
achieved glide, detour). Ranks and percentiles are against the pilots you
selected, not the whole field — comparing yourself with the three people you
chose is usually the question being asked.

## The map

deck.gl, driven from `pydeck`. Drag to pan, scroll to zoom, hover anything for
detail: the track reports time, altitude, climb rate and ground speed; airspace
reports its class and vertical limits; task legs and turnpoints name themselves.

The track is drawn as many short segments rather than one path, because deck.gl
picks whole paths and not vertices — a single path could only ever say "this is
the track". Segments break at every phase change and share their boundary point,
so the line has no gaps.

### Base maps

Terrain matters more than roads for soaring, so the default is **OpenTopoMap** —
contours, relief, lakes, rivers and peaks — rather than standard OSM carto, which
is also the tile service the OSM Foundation asks applications not to consume.
Also selectable:

| Style | What it adds |
|---|---|
| CARTO Voyager | roads, towns and labels; keyless, OSM-derived |
| swisstopo | far better in the Alps; keyless, free on localhost (a public deployment needs a WMTS account) |
| Esri World Imagery | satellite |

### Airspace

Read from an **OpenAIR file on disk**, not fetched. The file is the thing worth
pinning: a flight should be debriefed against the airspace as it was, and a live
fetch silently re-dates old flights. [openAIP](https://www.openaip.net/data/airspaces)
publishes OpenAIR exports per country, updated weekly — drop one into
`data/airspace/`.

Polygons are coloured by what the airspace means for a glider — restricted,
controlled, wave window, other — rather than by class letter, with the class in
the tooltip. Fills are faint because airspace stacks vertically and an opaque
fill would bury the track.

The altitude filter hides airspace the flight was never vertically near. Limits
given **above ground level are always kept**, because resolving them needs a
terrain model this project does not carry yet; the filter errs toward showing an
airspace rather than hiding one.

**Not for navigation.** openAIP's data is explicitly uncertified, flight levels
are converted at a flat 100 ft, and AGL limits are unresolved. This is a
post-flight overlay, never guidance.

## Running it safely

The app binds **loopback only** (`server.address = "127.0.0.1"` in
`.streamlit/config.toml`). Streamlit's own default listens on every interface,
which matters here because the sidebar lets whoever opens the app type a
directory path and list the flights under it — fine for you on your own machine,
not fine for everyone on club wifi. Change it to `0.0.0.0` only deliberately,
and only behind authentication.

Uploaded filenames are attacker-controlled, so they are reduced to a basename
and checked to land inside the target directory before anything is written; see
`_safe_upload_path` and `tests/test_upload_safety.py`.

IGC and OpenAIR parsing is plain text parsing — no `eval`, `exec` or `pickle` at
runtime in the parsing dependencies — so opening a stranger's flight log is not
itself risky. The residual risk is the usual one for any Python project: the
~50 packages this pulls in. Install into a project virtualenv (as below), not
system-wide.

Fetching map tiles tells the tile provider your IP and which tiles you asked
for, which is roughly where you fly. Use the offline-friendly base map or none
at all if that matters to you.

## Known constraints

- Airspace vertical limits are approximate: flight levels convert at a flat
  100 ft (pressure, not true altitude) and AGL limits are unresolved without a
  terrain model.
- `opensoar` 2.1.3 pins `aerofiles<1.5`, so `aerofiles` 1.5.x on PyPI cannot be
  used with it. Pinned accordingly in `pyproject.toml`.
- Multistart tasks are not scoreable — `opensoar` does not support them.
- AAT scoring works but is far less exercised here than race tasks.

## Testing

```bash
.venv/bin/python -m pytest
```

The SoaringSpot importer is tested against a **local HTTP server** serving a page
shaped like a real results page (`tests/fixtures/fake_soaringspot.py`), so
opensoar's actual scraping code runs end to end without the network. What that
cannot prove is that SoaringSpot's markup still looks like this — the standing
risk of scraping, and the reason the downloaded IGC files are the durable asset.

There are no real competition IGC files in the repo. `tests/fixtures/synthetic.py`
flies a synthetic glider around a 311 km triangle and writes a valid SoaringSpot
-flavoured IGC file. Because that glider has a *known* polar — 33 m/s cruise at
1.15 m/s sink, so L/D 28.7 — the fixture doubles as ground truth: the metric tests
assert the analysis recovers those numbers.

The thermals in the fixture are flown as real circles rather than by incrementing
altitude, because the PySoar detector triggers on >225° of consistent turn. A
fixture that simply climbed would parse cleanly and silently yield zero thermals.
