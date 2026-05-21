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

// knot ships zero runtime deps — same posture as the Python source.
// The Java port is a SQL string compiler: spec → SQL string. No JDBC,
// no ORM, no migration tool. Hosts wire those in.
dependencies {
    testImplementation(platform("org.junit:junit-bom:5.10.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testImplementation("org.assertj:assertj-core:3.26.3")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")

    // Postgres JDBC driver — testRuntimeOnly so integration tests
    // can run against the compose-managed postgres on :5433. Main
    // compile path never sees a driver.
    testRuntimeOnly("org.postgresql:postgresql:42.7.4")
}

tasks.test {
    useJUnitPlatform()
}

tasks.withType<JavaCompile>().configureEach {
    options.encoding = "UTF-8"
    options.release = 21
}
