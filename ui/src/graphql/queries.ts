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
      slots {
        name
        identifier
        required
        description
        pattern
        minimumValue
        maximumValue
        permissibleValues
        resolutionPolicy
        typeKind
        typeName
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
        description
      }
      sourceBindings {
        sourceName
        className
        identifierSlotName
        mappings {
          slotName
          sourceField
          nullSemantics
          hasPrior
        }
        trustPrior
        requiredSlotNames
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
