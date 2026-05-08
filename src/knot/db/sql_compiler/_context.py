"""CompileContext — shared state threaded through a compilation pass."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from knot.ontology.metaschema import OntologyClass


@dataclass
class CompileContext:
    """Mutable accumulator for one predicate compilation pass.

    ``primary_class`` is the OntologyClass whose rows are being validated.
    ``alias`` is the SQL table alias for the source-row table (default "s").
    ``params`` accumulates positional parameters in left-to-right emit order;
    callers read it after ``compile_predicate`` returns.
    """

    primary_class: OntologyClass
    alias: str = "s"
    params: list[Any] = field(default_factory=list)

    def with_subquery_alias(
        self,
        target_cls: OntologyClass,
        alias: str,
    ) -> "CompileContext":
        """Return a child context for compiling a predicate inside a subquery.

        The child shares the *same* ``params`` list so parameters accumulate
        in left-to-right emit order across the entire statement.
        """
        return CompileContext(
            primary_class=target_cls,
            alias=alias,
            params=self.params,
        )
