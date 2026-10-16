"""Persistent memory and project knowledge.

Two different things that are easy to confuse and are kept apart here:

* :class:`~codemind.memory.MemoryStore` — durable notes: architecture facts, your
  preferences, verified solutions, mistakes, task summaries. Retrieved by relevance when a
  new task starts.
* :class:`~codemind.memory.KnowledgeIndex` — a rebuildable index of the code itself
  (files, symbols, test suites), used to point the agent at the right files quickly.

Neither of them is a model. Training is a separate subsystem and the UI never calls memory
"training".
"""

from codemind.memory.memory import MEMORY_KINDS, MemoryEntry, MemoryStore
from codemind.memory.knowledge import KnowledgeIndex, ProjectIndex, analyze_repository

__all__ = ["MemoryStore", "MemoryEntry", "MEMORY_KINDS", "KnowledgeIndex", "ProjectIndex", "analyze_repository"]
