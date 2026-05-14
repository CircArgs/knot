plugins {
    `java-library`
}

group = "io.github.circargs"
version = "0.1.0-SNAPSHOT"

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}

repositories {
    mavenCentral()
}

dependencies {
    // SQL parsing + AST rewriting. Apache Calcite is the closest Java
    // analogue to sqlglot — used by knot.compile.constraints to walk
    // and rewrite spec-relative class/slot references in constraint
    // bodies and source-binding mappings.
    implementation("org.apache.calcite:calcite-core:1.37.0")

    // JSON serialization for the spec's meta-table jsonb roundtrip
    // and for the spec_io save / load path.
    implementation("com.fasterxml.jackson.core:jackson-databind:2.17.2")

    // jOOQ is the host substrate knot consumes for entity-table DDL,
    // FK constraints, batch INSERTs, and migrations (via Flyway). The
    // dependency is declared so knot.compile can consume jOOQ's
    // generated metadata; knot does not embed jOOQ runtime behavior.
    implementation("org.jooq:jooq:3.19.11")

    testImplementation(platform("org.junit:junit-bom:5.10.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    useJUnitPlatform()
}

tasks.withType<JavaCompile>().configureEach {
    options.encoding = "UTF-8"
    options.release = 21
}
