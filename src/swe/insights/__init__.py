"""Workflow insights: per-session metrics derived from the search index.

``insights.sqlite`` is a rebuildable store under ``~/.swe/data/insights/``.
The T0 pass fills ``session_metrics`` counters from the index's own
``messages``/``messages_fts`` rows; a later trigger-driven pass scores
sessions still holding ``outcome=NULL``. See ``docs/insights.md``.
"""
