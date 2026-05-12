/**
 * TypeNode is no longer used — primitive types are language-level builtins,
 * not spec entities, so there are no type nodes in the graph.
 * This file is kept to avoid breaking any stale import references during
 * the transition; it renders nothing.
 */
import type { NodeProps } from "@xyflow/react";

export default function TypeNode(_props: NodeProps) {
  return null;
}
