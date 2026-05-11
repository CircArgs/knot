import { ApolloClient, InMemoryCache, HttpLink } from "@apollo/client";

export const apolloClient = new ApolloClient({
  link: new HttpLink({ uri: "/spec/graphql" }), // proxied via vite to knot:8000
  cache: new InMemoryCache(),
});
