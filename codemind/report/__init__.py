"""Reporting: the evidence CodeMind hands over for the work it claims to have done.

Two things live here, both about trust rather than features:

* :mod:`codemind.report.quality` — the honesty metric: of the tasks reported as completed, how many
  carry verification evidence, and which ones do not.
* :mod:`codemind.report.passport` — the *change passport*: a self-contained, hash-chained record of
  one task (plan, steps, commands, diffs, checks, models used) that can be kept, mailed or attached
  to a pull request, and that can be checked for tampering.
"""

from codemind.report.passport import build_passport, render_markdown, verify_chain, write_passport
from codemind.report.quality import honesty_report, unverified_tasks

__all__ = [
    "build_passport",
    "honesty_report",
    "render_markdown",
    "unverified_tasks",
    "verify_chain",
    "write_passport",
]
