import { gql } from "@apollo/client";

/**
 * Full published spec — every field that nodes/edges/property-panel need.
 * Mirrors `knot.api.spec_graphql.PublishedSpec`.
 */
const SLOT_FIELDS = gql`
  fragment SlotFields on SlotGQL {
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
`;

export const PUBLISHED_SPEC = gql`
  ${SLOT_FIELDS}
  query PublishedSpec {
    publishedSpec {
      id
      version
      revision
      contentHash
      classes {
        name
        abstract
        description
        definition
        isAName
        mixinNames
        slots {
          ...SlotFields
        }
        effectiveSlots {
          ...SlotFields
        }
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
