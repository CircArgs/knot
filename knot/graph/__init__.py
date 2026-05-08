"""Graph submodule — data-plane orchestration logic.

Pure composition over ``knot.db`` primitives — no SQL strings, no
``psycopg`` imports. The persistence layer stays under ``knot.db``;
this module ties it together (trust-resolved reads, correction
orchestration with bandit feedback emission, etc.).
"""
