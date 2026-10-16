"""
The data engine: pure functions over files and Polars DataFrames.

Nothing in this package knows about HTTP, users, the database or Storage,
which makes it easy to test and to reason about.
"""
