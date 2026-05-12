/**
 * Cascade helpers for delete + add (no PATCH) updates and constrained deletes.
 *
 * Used by `pages/SpecGraph.tsx` when the user double-clicks a non-class node
 * to edit it, or right-clicks to delete with cascade-detachment.
 */

import type { PublishedSpec } from "../types/spec";
import * as api from "./draftApi";

/**
 * For non-class entities (Source, Constraint), edit = delete + add.
 */
export type EditableKind = "source" | "constraint";

export async function editEntityViaDeleteAdd(
  draftId: number,
  _spec: PublishedSpec,
  kind: EditableKind,
  oldName: string,
  performAdd: () => Promise<unknown>,
): Promise<void> {
  if (kind === "source") {
    await api.deleteSource(draftId, oldName);
    await performAdd();
    return;
  }
  if (kind === "constraint") {
    await api.deleteConstraint(draftId, oldName);
    await performAdd();
    return;
  }
}

/**
 * Auto-detach handler used by the cascade-delete flow. Given a 409 referenced
 * error, detaches all references to the named entity and retries.
 *
 * This is a structural heuristic because knot core's `ReferencedEntityError`
 * yields a free-form string. We over-detach (anything referencing) rather
 * than parse the message verbatim.
 */
export async function autoDetach(
  draftId: number,
  spec: PublishedSpec,
  kind: EditableKind | "class",
  name: string,
): Promise<void> {
  if (kind === "class") {
    // Detach is_a / mixins from any class that points here.
    for (const c of spec.classes) {
      const patch: api.ClassUpdate = {};
      let dirty = false;
      if (c.isAName === name) {
        patch.is_a_name = null;
        dirty = true;
      }
      if (c.mixinNames.includes(name)) {
        patch.mixin_names = c.mixinNames.filter((m) => m !== name);
        dirty = true;
      }
      if (dirty) await api.updateClass(draftId, c.name, patch);
    }
    return;
  }
  // source/constraint are leaf-ish; no cascade structurally.
}
