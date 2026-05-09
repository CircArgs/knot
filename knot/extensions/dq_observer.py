"""Builtin extension — records DQ observations post-INSERT.

Registers on RowsIngested at priority 100 (after the constraint validator
at 50, so DQ only writes if validation passed). Side-effects via
``ctx.db.dq.record_incremental`` — runs inside the route's transaction
so observations roll back consistently with the INSERT if a downstream
handler raises.
"""

from __future__ import annotations

from knot.extensions import RequestContext, dispatch
from knot.extensions.events import RowsIngested


@dispatch.on(RowsIngested, priority=100)
async def _record(ev: RowsIngested, ctx: RequestContext) -> None:
    cls = ev.source.entity_class
    rows_dump = [r.model_dump(exclude_none=False) for r in ev.rows]
    await ctx.db.dq.record_incremental(
        source_name=ev.source.name,
        cls=cls,
        batch_id=ctx.request_id,
        rows=rows_dump,
    )
