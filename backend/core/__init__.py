"""
Production pipeline for Alphapoly.

Single-process incremental pipeline that:
- Loads models once, keeps in memory
- Uses SQLite for O(1) state lookups
- Processes only new events
- Merges results into accumulated _live/ state
"""

__version__ = "0.1.0"
