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
    trace.py    slicing a trace by time, by binary search
    metrics.py  the four metric families
    compare.py  a field over one task: leg deltas and distributions
    progress.py task distance over time, and the gap to a reference
    field.py    the whole field: the lift map, the routes, the composite
  sources/    importers; one per place flights come from
  app/        Streamlit UI, Plotly charts, pydeck maps
    format.py   numbers and times, formatted one way for the whole app
    theme.py    the palettes and the base maps
    charts.py   Plotly figures
    maps/       one module per map: base, layers, flight, gap, field
    day_view/   one module per tab: archive, tables, gap_tab, field_tab, view
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

## Replay

Both views have a **Replay** tab: a time cursor shared by a map and a barogram,
with a plane per pilot, play/pause, 1×–20× speed, forward or backward, and
drag anywhere on the barogram to scrub.

It is a self-contained page embedded with `components.html`, and it uses **no
external libraries** — the map and the chart are drawn on canvas. Two reasons.
Streamlit re-runs the whole script on every interaction, so a slider-driven
animation would re-render the deck and the chart on every frame: choppy at 1×,
pointless at 20×, and unable to express dragging on a chart at all. And a
library pulled from a CDN is code that cannot be verified where the CDN is
unreachable, which is how an earlier map bug shipped unnoticed.

The replay draws raster tiles, so every basemap carries a raster URL alongside
its vector style. The plane's position is interpolated between samples, so
thinning the trace sets payload size rather than smoothness.

## Comparing a day

Switch the sidebar to **Compare a day** to put a field side by side over one
task. Pick the competition, class and day from the archive, then choose up to
twenty pilots. The first eight colours clear every colour-blind separation
check; the remaining twelve are as far apart as twenty categories can be, which
is not far enough — the worst pair measures ΔE 11.1 against a floor of 15. Past
eight the picker says so, and the tables carry every number as text.

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

## Where the time went

A leg delta says *which leg* cost you three minutes. The **Where the time went**
tab says where in the leg, by swapping the ruler: instead of comparing two
pilots at the same moment — meaningless when they started twenty minutes apart
and are forty kilometres from each other — it compares them at the same *point
on the course*.

```
delta(d) = (time you took to reach task distance d)
         - (time the reference took to reach task distance d)
```

`delta` is flat where two pilots progressed equally and rises where one fell
behind, so its slope is the answer. Over any stretch the rise is exactly the
seconds lost on that stretch, which is what the map colours, what the chart
plots and what the table counts — the three agree by construction, and they
agree with the leg deltas too, because the same seconds are being counted at a
finer resolution.

Task distance is measured the way a scorer measures it: how far along the
declared course the glider has got, not how far it has flown. Flying away from
the next turnpoint earns nothing, so a detour costs distance rather than
silently adding it, and the measure is forced never to decrease so that "when
did you first reach here" has one answer even while circling. Both pilots are
read the same way, on first arrival — which is why a pilot measured against
themselves comes out as exactly zero.

Four controls:

| Control | What it changes |
|---|---|
| Whose flight | the track on the map. One pilot, not the field: the colour channel is spent on polarity, so it has none left for identity |
| Measured against | one rival, or **the field's best** (the quickest anyone got to each point — a ceiling nobody flew) or **the field's median** |
| Colour the track by | **time**, which is what the day is scored on, or **height** at the same point on course, which is usually the cause of which lost time is the symptom |
| Stretch length | how much course each coloured piece covers |

Stretch length is the one worth understanding. Two pilots almost never climb in
the same place, so at 2 km you mostly see *who stopped where*: red where you
climbed, blue where the reference did, and the pair says nothing about who was
quicker. That resolution is for finding the one thermal that cost the day. Ten
kilometres — the default — holds a climb and the glide it buys, which is the
comparison that answers the question. The colour thresholds scale with the
stretch (1 s/km to leave the neutral band, 4 s/km to reach the far end), so the
same flying reads the same way at every setting.

Colour is never the only channel. The track thickens with the size of the
difference, the five worst stretches are ringed, hovering gives the numbers, and
the table lists them with the clock time, the time into the task and the
turnpoint they happened before.

## The day's map

Two maps built from the *whole* class, not from the pilots you ticked — a map
of the lift drawn from four flights is a map of four flights.

**Where the lift was** pools every climb the field found, bins them into
equal-area cells and shades each cell by the average climb rate in it. The
tooltip says how many climbs and how many pilots that average rests on, which
is the difference that matters: a dark cell visited once is one pilot's good
luck, a dark cell visited by eight is where the day was. Climbs under 100 m or
45 s are dropped — the phase detector counts a bump taken in a turn as a climb,
and by number those would swamp the decisions a pilot actually made.

**Where they flew** pools the flying *between* the climbs and shades by how
many different pilots crossed each cell. Circling is excluded, so this is the
map of chosen routes rather than a second map of thermals, and counting pilots
rather than fixes stops a slow glider outvoting a fast one.

Cells are binned in an azimuthal equidistant projection centred on the flights
themselves, so a 5 km cell is 5 km at every competition site. Binned in
degrees it would not be, and two sites could not be compared at all.

## The best that was there

The fastest anyone covered each stretch of the course, assembled into one
route. Nobody flew it: it is the day's ceiling, and the gap between it and the
quickest actual flight is what was left on the table.

The construction has one rule that makes it worth looking at, and it is not
obvious. Simply taking the quickest crossing of every stretch builds a glider
that never climbs — at every stretch *somebody* was gliding through, so the
minimum always picks a glide, and the result flies the whole task without
stopping and "beats" the winner by seventy minutes. That is not a ceiling, it
is an artefact of taking a minimum over a population.

So the composite carries a height. It may only use a piece whose pilot entered
it no higher than the composite currently is — you cannot borrow a glide that
started above you — and it leaves having lost exactly what that pilot lost,
capped at the highest anyone reached there. Climbs stop being optional: the
composite has to buy its altitude the same way everyone else did, out of the
lift that was actually there. It is a shortest-path search over (stretch,
height) rather than a greedy pass, because the quickest crossing of a stretch
is often the one that arrives lowest, and paying thirty seconds for height here
is regularly what makes the next two stretches possible.

Two properties fall out, and both are tested: every single pilot's own flight
is a legal path through the search, so the composite is **never slower than the
quickest of them**; and finer pieces never produce a slower composite, because
every coarse piece is a run of fine ones.

It is still a ceiling and not a plan. Its pieces were flown by different pilots
in different gliders at different times of day, and the ringed joins on the map
are where it changes hands. The table below it says who supplied what — a
composite dominated by one name is a day somebody simply flew better; one
spread across ten is a day where the winner was whoever strung together the
most of what was available. Make the pieces long enough and it collapses to the
winner's actual flight, which is the sanity check on the whole idea.

Two things it does **not** know, both of which matter before you read it as
achievable:

- **Polars.** A glide borrowed from a JS3 is not reproducible in a Discus. The
  composite records which glider flew each piece so you can see when this
  applies, but it does not model performance. Feeding it real polars is the
  natural next step — see *Known constraints*.
- **Wing loading.** It is not in an IGC file at all: the format carries no mass
  and no ballast state. Nothing here can infer it.

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
- Engine noise (ENL/MOP) is read from the file but not checked, so a
  self-launcher's numbers are not automatically flagged as unpowered.
- **No glider performance model.** Nothing here knows a polar, so it cannot say
  whether a glide one pilot achieved was available to another, cannot compute
  the speed-to-fly the day's climbs called for, and cannot compare across
  gliders except by the times they actually flew. The pieces needed are all
  public: XCSoar's `PolarStore` carries reference mass, maximum ballast, three
  (speed, sink) points, wing area and a contest handicap for around 250 types.
  It is GPL-2.0-or-later, so it would be fetched rather than vendored into this
  tree. With it, three analyses become possible that are impossible now:
  speed-to-fly against the climbs each pilot actually got, handicapped
  comparison across types, and the wing loading that would have suited the
  day's climb rates.
- **Wing loading is not in an IGC file.** The format records no mass and no
  ballast state, so it cannot be derived from a trace — only entered, or taken
  from a source that publishes it.

## Performance

A competition day is twenty flights of ten thousand fixes, and analysis is the
slow part. Two things make it bearable, and one of them was worth an afternoon:

**Fix timestamps are re-stamped with the standard library's UTC at load.**
`aerofiles` attaches its own `TimeZoneFix`, whose `utcoffset` builds a fresh
`timedelta` on every call — and every comparison between two aware datetimes
calls it. It was the single most expensive thing this project did: 10 of 18
seconds of a day's field analysis, across 16 million calls. `datetime.timezone.utc`
answers the same question from C, and aerofiles says in the class itself that it
exists for Python 2.6 and can be replaced once 3.x is the minimum, which it is
here.

**Trace slicing is a binary search, not a scan** (`core/trace.py`). "What
happened between these two moments" is asked once per leg, once per thermal and
once per stretch of course; written as a comprehension it costs a full pass
each time.

Measured on twenty synthetic flights of 10,160 fixes each:

| | before | after |
|---|---|---|
| load + analyse the day | 6.2 s | 3.5 s |
| the gap to a reference | 2.2 s | 0.4 s |
| pool the field's climbs | 4.4 s | 0.01 s |
| pool the field's routes | 4.2 s | 0.04 s |
| build the composite | 1.9 s | 0.2 s |

What remains in `load + analyse` is inside `aerofiles` and `opensoar` — a
`strptime` per B record and a geodesic call per fix pair — so the lever there is
not to do it twice. The day's analysis is cached on the files' paths and
modification times, which is why switching back to a day you already looked at
is instant. Analysing a day is also embarrassingly parallel across flights and
is not parallelised; that is the next thing to try if it ever needs to be
faster.

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
