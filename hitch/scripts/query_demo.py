"""After run_demo: hit the GraphQL API + raw resolved view to prove
everything works end-to-end.

Doesn't go through the knot library — uses requests so it exercises
the *running API*. Compare with the in-process query in
notebooks/06_end_to_end if you want the substrate-only path.
"""

from __future__ import annotations

import json
import sys

import requests

API = "http://localhost:8000/graphql"

_QUERY = """
{
  movieList(first: 20) {
    canonical_id
    title
    year
    runtime_minutes
    director {
      canonical_id
      name
      birth_country
    }
  }
}
"""


def main() -> None:
    r = requests.post(API, json={"query": _QUERY}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        print("GRAPHQL ERRORS:")
        print(json.dumps(data["errors"], indent=2))
        sys.exit(1)
    print(json.dumps(data["data"], indent=2))


if __name__ == "__main__":
    main()
