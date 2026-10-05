"""Incident engine (architecture layer 7, Stage 6, ADR-041): correlation, lifecycle, digest items, impact.

The core (``correlate``, ``lifecycle``, ``engine``) is a pure fold over a time-ordered event stream: code correlates
and moves state, humans close (ADR-028). ``pipeline`` builds the stream from Stages 3-5, ``impact`` estimates impact
through ``metrics.compute``, ``storage`` appends rows to ClickHouse. Nothing here reads ground truth or the clock.
"""
