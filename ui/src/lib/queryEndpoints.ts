/**
 * The two GraphQL surfaces the query page hits.
 *
 * - `spec`  → `/spec/graphql`   — static schema introspected from the spec service
 * - `data`  → `/graph/query`    — data-plane schema compiled per published spec
 *
 * Both share a single shape; everything else (introspection cache, default
 * query, localStorage key) keys off this registry so adding a third surface
 * is a single-entry change.
 */

export type EndpointKey = "spec" | "data";

export interface EndpointDef {
  /** Display name for the dropdown. */
  label: string;
  /** Server path, relative — vite proxy / same-origin in prod. */
  path: string;
  /** Default query loaded the first time this endpoint is selected. */
  defaultQuery: string;
}

export const ENDPOINTS: Record<EndpointKey, EndpointDef> = {
  spec: {
    label: "spec",
    path: "/spec/graphql",
    defaultQuery: `# Static spec schema. Try fields on \`publishedSpec\`.
{
  publishedSpec {
    id
    version
    revision
    contentHash
    classes {
      name
      slotNames
    }
  }
}
`,
  },
  data: {
    label: "data",
    path: "/graph/query",
    defaultQuery: `# Data-plane schema (compiled per published spec).
# Class-row fields appear named after each concrete class
# (e.g. \`movie\`, \`movieByCanonicalId\`, \`movieResolved\`).
{
  movie(limit: 10)
}
`,
  },
};
