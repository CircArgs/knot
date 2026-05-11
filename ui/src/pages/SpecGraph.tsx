/**
 * Placeholder for the spec-graph visualization.
 *
 * Will use @xyflow/react (already in deps) to render OntologyClass
 * nodes + inheritance / slot-range / source edges. Layered out via a
 * dagre-style positioning pass driven by the published spec graph.
 */
export default function SpecGraph() {
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-2">spec graph</h1>
      <p className="text-sm text-knot-muted">
        Placeholder. Will render the published Spec as a class/edge graph
        via @xyflow/react.
      </p>
    </div>
  );
}
