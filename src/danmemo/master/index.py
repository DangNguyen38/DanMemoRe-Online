"""Bounded projections of original master bytes, never operator/account state.

The exact bytes and field layout form the cache key. Changed master content
cannot reuse an older projection; returned tuples cannot be mutated by callers.
"""

import functools
from danmemo.master import reader as master


@functools.lru_cache(maxsize=32)
def project(data, fields):
    return tuple(
        (
            tuple(
                (
                    row.text(field) if width == "text" else row.number(field, width)
                    for field, width in fields
                )
            )
            for row in master.rows(data)
        )
    )


def columns(name, *fields):
    return project(master.master_bytes(name), fields)
