"""The Lamptown desktop app: the town in its own window, kept live (``lamptown app``).

Modules: :mod:`.live` (rebuild loop, data fingerprint, visibility; no GUI import), :mod:`.window`
(pywebview glue and the browser fallback), :mod:`.instance` (one copy per user), :mod:`.paths`
(per-OS state folders), :mod:`.applog` (capped log), :mod:`.shortcut` (``lamptown shortcut``) and
:mod:`.cli` (the two commands). The icons in ``assets/`` are drawn by ``tools/make_icon.py``.
"""
