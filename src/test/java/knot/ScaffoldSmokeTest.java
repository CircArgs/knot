package knot;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * Trivial scaffold-only test so {@code ./gradlew test} has something
 * to run before the real metaschema + emitters land. Delete this file
 * once any real {@code knot.spec} or {@code knot.compile} test exists.
 */
class ScaffoldSmokeTest {

    @Test
    void buildPipelineIsWired() {
        assertEquals(2, 1 + 1, "the JVM has not lost its mind");
    }
}
