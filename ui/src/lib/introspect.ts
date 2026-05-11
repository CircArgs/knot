/**
 * Run a GraphQL introspection query against an endpoint and return both the
 * raw introspection JSON and a `GraphQLSchema` object built from it.
 *
 * The schema object is what `monaco-graphql` wants for autocomplete/hover.
 * The raw introspection is what the schema explorer renders (we keep the JSON
 * because walking the JSON is simpler than walking the schema object for a
 * tree view).
 */
import {
  buildClientSchema,
  getIntrospectionQuery,
  type GraphQLSchema,
  type IntrospectionQuery,
} from "graphql";

export interface IntrospectionResult {
  introspection: IntrospectionQuery;
  schema: GraphQLSchema;
}

export async function introspectEndpoint(
  url: string,
): Promise<IntrospectionResult> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query: getIntrospectionQuery() }),
  });
  if (!r.ok) throw new Error(`introspection ${url} ${r.status}`);
  const body = (await r.json()) as { data?: IntrospectionQuery; errors?: unknown[] };
  if (body.errors && body.errors.length > 0) {
    throw new Error(`introspection errors: ${JSON.stringify(body.errors)}`);
  }
  if (!body.data) throw new Error("introspection: empty data");
  const schema = buildClientSchema(body.data);
  return { introspection: body.data, schema };
}

/** POST a GraphQL operation and return the parsed body (data + errors). */
export async function runGraphQL(
  url: string,
  query: string,
  variables?: Record<string, unknown>,
): Promise<{ data?: unknown; errors?: unknown[] }> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query, variables: variables ?? {} }),
  });
  // GraphQL servers can still return 200 with `errors` set; surface either.
  const body = await r.json().catch(() => ({}));
  if (!r.ok && !body?.errors) {
    return { errors: [{ message: `HTTP ${r.status}` }] };
  }
  return body;
}
