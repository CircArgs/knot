package knot.ast.types;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class PrimitiveTest {

    @Test
    void sqlNameMatchesPythonStrEnumValue() {
        assertThat(Primitive.TEXT.sqlName()).isEqualTo("text");
        assertThat(Primitive.INTEGER.sqlName()).isEqualTo("integer");
        assertThat(Primitive.FLOAT.sqlName()).isEqualTo("float");
        assertThat(Primitive.BOOLEAN.sqlName()).isEqualTo("boolean");
        assertThat(Primitive.DATE.sqlName()).isEqualTo("date");
        assertThat(Primitive.TIMESTAMP.sqlName()).isEqualTo("timestamp");
    }

    @Test
    void isATypeExpression() {
        TypeExpression t = Primitive.TEXT;
        assertThat(t).isInstanceOf(Primitive.class);
    }

    @Test
    void toStringRendersSqlName() {
        assertThat(Primitive.INTEGER.toString()).isEqualTo("integer");
    }
}
