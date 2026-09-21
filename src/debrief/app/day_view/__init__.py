"""The compare-a-day view, one module per tab.

``archive``
    finding a day on disk and analysing it, cached
``tables``
    the tables and colour legends under the maps
``gap_tab``
    where the time went, against one rival or against the field
``field_tab``
    the whole class pooled: the lift map, the route map, the composite
``view``
    the page itself — the pickers, the tabs, and what each one is handed

``render`` is the only thing the rest of the application calls.
"""

from debrief.app.day_view.view import MAX_COMPARED, render

__all__ = ["MAX_COMPARED", "render"]
