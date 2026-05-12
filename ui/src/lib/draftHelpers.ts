/**
 * Cascade helpers for delete + add (no PATCH) updates and constrained deletes.
 *
 * Used by `pages/SpecGraph.tsx` when the user double-clicks a non-class node
 * to edit it, or right-clicks to delete with cascade-detachment.
 */

import type { PublishedSpec } from "../types/spec";
import * as api from "./draftApi";

/**
 * Detach a slot from every class that lists it, run `mutate`, then re-attach.
 * Used to delete+add a slot while preserving its references.
 */
export async function withSlotDetached<T>(
  draftId: number,
  spec: PublishedSpec,
  slotName: string,
  mutate: () => Promise<T>,
): Promise<T> {
  const attachedClasses = spec.classes.filter((c) => c.slotNames.includes(slotName));
  // Detach.
  for (const c of attachedClasses) {
    await api.updateClass(draftId, c.name, {
      slot_names: c.slotNames.filter((s) => s !== slotName),
    });
  }
  try {
    return await mutate();
  } finally {
    // Re-attach (best-effort; if mutate created a slot with the same name,
    // restoring is correct; if it failed, we still try to put references back).
    for (const c of attachedClasses) {
      try {
        await api.updateClass(draftId, c.name, {
          slot_names: c.slotNames,
        });
      } catch {
        /* swallow — slot may not exist if mutate aborted */
      }
    }
  }
}

/**
 * For non-class entities (Slot, Source, Constraint), edit = delete + add
 * with detaching of any referencing class slot_names. Returns the new spec
 * after the mutation, by ID.
 */
export type EditableKind = "slot" | "source" | "constraint";

export async function editEntityViaDeleteAdd(
  draftId: number,
  spec: PublishedSpec,
  kind: EditableKind,
  oldName: string,
  performAdd: () => Promise<unknown>,
): Promise<void> {
  if (kind === "slot") {
    await withSlotDetached(draftId, spec, oldName, async () => {
      await api.deleteSlot(draftId, oldName);
      await performAdd();
    });
    return;
  }
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
 * error, the body string typically lists the references; we parse it best-
 * effort by checking which entities mention the name, then PATCH them off.
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
  if (kind === "slot") {
    for (const c of spec.classes) {
      if (c.slotNames.includes(name)) {
        await api.updateClass(draftId, c.name, {
          slot_names: c.slotNames.filter((s) => s !== name),
        });
      }
    }
    return;
  }
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
