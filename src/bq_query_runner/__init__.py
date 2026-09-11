"""
bq_query_runner runs SQL scripts on Google BigQuery from files.

Features: multi-statement SQL files, ${...} placeholder substitution, plain
text replacements, dry-run mode, resume-on-error and optional export of the
query results (parquet, csv or json).

The code and the ``main`` entry point live in :mod:`bq_query_runner.runner`.
"""

__version__ = "0.1.0"

from .runner import main
