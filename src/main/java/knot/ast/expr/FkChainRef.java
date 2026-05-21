package knot.ast.expr;

import java.util.ArrayList;
import java.util.List;

/**
 * Reference reached by walking one or more FK hops to a terminal slot on the
 * final class.
 *
 * <p>The compiler emits the necessary JOINs at query-render time; this node
 * just records the path. Validation of slot existence and FK-ness happens at
 * compile time.
 *
 * <p>Example — Python {@code Movie.col.director.name}:
 * <pre>
 *   sourceClass  = "Movie"
 *   chain        = [Hop("director", "Person")]
 *   terminalSlot = "name"
 * </pre>
 *
 * <h2>Deviation from Python</h2>
 *
 * <p>The Python source uses nested {@code __getattr__} so chains compose
 * transparently. Java exposes {@link #walk(String, String, String)} to append
 * one more hop — pick {@code fkSlot}, the {@code targetClass} it points at,
 * and the new {@code terminalSlot}. Repeat for multi-hop.
 */
public record FkChainRef(String sourceClass, List<Hop> chain, String terminalSlot)
        implements ValueExpr {

    /** One step of the chain: {@code fkSlotName} on the current class, walking to {@code targetClassName}. */
    public record Hop(String fkSlotName, String targetClassName) {
        public Hop {
            if (fkSlotName == null || fkSlotName.isBlank()) {
                throw new IllegalArgumentException("Hop.fkSlotName must be non-blank");
            }
            if (targetClassName == null || targetClassName.isBlank()) {
                throw new IllegalArgumentException("Hop.targetClassName must be non-blank");
            }
        }
    }

    public FkChainRef {
        if (sourceClass == null || sourceClass.isBlank()) {
            throw new IllegalArgumentException("FkChainRef.sourceClass must be non-blank");
        }
        if (chain == null || chain.isEmpty()) {
            throw new IllegalArgumentException("FkChainRef.chain must be non-empty");
        }
        if (terminalSlot == null || terminalSlot.isBlank()) {
            throw new IllegalArgumentException("FkChainRef.terminalSlot must be non-blank");
        }
        chain = List.copyOf(chain);
    }

    /**
     * Extend the chain by one hop, replacing the terminal slot.
     *
     * <p>Existing chain points at the current terminal class; new hop walks
     * {@code fkSlot} from that class to {@code targetClass}, and the result
     * projects {@code terminalSlot} on the new target.
     */
    public FkChainRef walk(String fkSlot, String targetClass, String terminalSlot) {
        var extended = new ArrayList<Hop>(chain.size() + 1);
        extended.addAll(chain);
        extended.add(new Hop(fkSlot, targetClass));
        return new FkChainRef(sourceClass, extended, terminalSlot);
    }
}
