/**
 * GraphQL surface for the query page.
 *
 * Just the data plane — spec exploration lives in the spec-graph page.
 * Kept as a registry-shaped export so adding a future endpoint stays a
 * one-entry change.
 */

export type EndpointKey = "data";

export interface EndpointDef {
  label: string;
  path: string;
  defaultQuery: string;
}

export const ENDPOINTS: Record<EndpointKey, EndpointDef> = {
  data: {
    label: "data",
    path: "/graph/query",
    defaultQuery: `# Data-plane schema (compiled per published spec).
# Class-row fields appear named after each concrete or defined class
# (e.g. \`movie\`, \`credit\`, \`person\`, \`director\`).
#
# Forward traversal: credit → movie/person.
# Back traversal:    movie → credits.
# Filtering through relations:
#   credit(where: { movie: { title: { eq: "Inception" } } })
# Ordering through relations:
#   credit(orderBy: [{ field: movie_year, direction: DESC }])
{
  credit(limit: 10) {
    creditId
    role
    movie { title year }
    person { name born }
  }
}
`,
  },
};
