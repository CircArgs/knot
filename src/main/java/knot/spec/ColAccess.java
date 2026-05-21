package knot.spec;

import knot.ast.expr.FkRef;
import knot.ast.expr.Ref;
import knot.ast.expr.ValueExpr;
import knot.ast.expr.VectorRef;
import knot.ast.types.ClassRef;
import knot.ast.types.Vector;

/**
 * {@code cls.col().get("year")} returns a {@link Ref} for primitive/array
 * slots, an {@link FkRef} for FK slots (navigable for transparent chained
 * access via {@code FkRef.get(name)}), and a {@link VectorRef} for vector
 * slots.
 *
 * <p>Mirrors the Python {@code _ColAccess} helper. Python uses
 * {@code __getattr__} so {@code cls.col.year} works; Java has no
 * {@code __getattr__} so the API is explicit method calls:
 * {@code cls.col().get("year")}.
 *
 * <p>Typos raise {@link IllegalArgumentException} at lookup time (via
 * {@link OntologyClass#getSlot(String)}'s {@code KeyError}-equivalent).
 */
public final class ColAccess {

    private final OntologyClass cls;

    ColAccess(OntologyClass cls) {
        this.cls = cls;
    }

    /**
     * Look up the slot by name and produce the right {@link ValueExpr} subtype:
     * {@link FkRef} for {@link ClassRef} slots, {@link VectorRef} for
     * {@link Vector} slots, plain {@link Ref} otherwise.
     */
    public ValueExpr get(String slotName) {
        var slot = cls.getSlot(slotName);
        var type = slot.type();
        if (type instanceof ClassRef cr) {
            return new FkRef(cls.name(), slotName, cr.target().name());
        }
        if (type instanceof Vector v) {
            return new VectorRef(cls.name(), slotName, v.metric(), v.dim());
        }
        return new Ref(cls.name(), slotName);
    }
}
