"""gitkb — LLM-summarized knowledge base of a git repository's history.

Every commit becomes one Markdown note plus one note per file change, each
named by the sha256 of the exact text that was summarized. A SQLite index
(rebuildable from the notes) links commits, files and notes together.
"""

__version__ = "0.1.0"
