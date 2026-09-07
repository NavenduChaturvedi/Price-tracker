"""Optional local web dashboard for the price tracker.

A thin Flask layer over the same ``price_tracker`` package the CLI uses - it
adds no business logic of its own, it just renders what ``db`` and ``core``
already provide and posts back to them.

Run it with:  python -m webapp   (from the repo root)
"""
