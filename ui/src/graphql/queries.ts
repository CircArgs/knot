import { gql } from "@apollo/client";

/**
 * Full published spec — every field that nodes/edges/property-panel need.
 * Mirrors `knot.api.spec_graphql.PublishedSpec`.
 */
export const PUBLISHED_SPEC = gql`
  query PublishedSpec {
    publishedSpec {
      id
      version
      revision
      contentHash
      types {
        name
        base
        pattern
        description
      }
      slots {
        name
        identifier
        required
        multivalued
        description
        pattern
        minimumValue
        maximumValue
        permissibleValues
        resolutionPolicy
        rangeKind
        rangeName
      }
      classes {
        name
        abstract
        description
        isAName
        mixinNames
        slotNames
      }
      sources {
        name
        entityClassName
        identifierSlotName
        description
      }
      constraints {
        name
        primaryClassName
        severity
        message
      }
    }
  }
`;
