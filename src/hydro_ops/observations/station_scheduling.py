"""Bounded process-preparation collection without head-of-line blocking."""
from concurrent.futures import FIRST_COMPLETED, wait


def pop_completed(pending):
    """Remove one ready task, waiting only when every queued task is unfinished."""
    if not pending:
        raise ValueError('No pending preparations')
    if not any(item[0].done() for item in pending):
        wait([item[0] for item in pending], return_when=FIRST_COMPLETED)
    index = next(i for i, item in enumerate(pending) if item[0].done())
    return pending.pop(index)
