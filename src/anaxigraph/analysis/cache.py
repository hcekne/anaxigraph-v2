"""Bounded derived views keyed by graph identity without retaining their graph owners."""

from collections.abc import Callable
from functools import wraps
from threading import RLock
from typing import TypeVar
from weakref import ref

from anaxigraph.contracts import Graph

T = TypeVar("T")


def graph_cached(build: Callable[[Graph], T]) -> Callable[[Graph], T]:
    views: list[tuple[ref[Graph], T]] = []
    lock = RLock()

    @wraps(build)
    def cached(graph: Graph) -> T:
        with lock:
            views[:] = [(owner, value) for owner, value in views if owner() is not None]
            for owner, value in views:
                if owner() is graph:
                    return value
            value = build(graph)
            views[:] = [(ref(graph), value), *views[:1]]
            return value

    return cached
