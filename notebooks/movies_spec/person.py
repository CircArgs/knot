"""Cross-domain Person extensions.

Person is declared in ``base.py`` with the minimum the v1 spec
needs (``name``, ``birth_country``). Multiple downstream domains
(movies, games, podcasts, …) all reference Person, and each
contributes slots Person didn't originally carry. Rather than have
every domain extend Person separately (slot-ordering conflicts,
duplicate-add errors), centralize the cross-domain Person slots
here.

knot's ``cls.slot(...)`` is just an in-place mutation; this file's
import grafts the additional slots onto the existing class object.
"""

from knot import types
from movies_spec.base import person

person.slot("birth_year", types.INTEGER)
person.slot("role_description", types.TEXT)  # podcasts feed shape
person.slot("role_summary", types.TEXT)  # games feed shape
person.slot("name_embedding", types.VECTOR(384))
