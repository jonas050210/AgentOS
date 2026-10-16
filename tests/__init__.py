"""CodeMind's automated checks.

Run them with the standard library only:

    python3 -m unittest discover -s tests -v

Each module is independent and uses a temporary storage root, so running the suite never touches
your real data. The suite is deliberately lightweight (no model downloads, no GPU, no network)
and finishes in well under a minute on a two-core machine.
"""
