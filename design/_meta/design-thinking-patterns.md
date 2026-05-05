# Design-thinking patterns observed during knot design

**Status:** internal-use meta doc. Captures recurring patterns in how the user (Nick) confronts design problems during knot's design — and the personas a subagent can adopt to think the same way during design review brainstorming.

This is not part of knot's design. It's about how the design *itself* is conducted. The goal: when design review surfaces issues, dispatch persona subagents that approach solutions the way Nick would, faster.

---

## The patterns

Each pattern is something Nick consistently did across multiple conversations. The "I (Claude)" behavior is what I defaulted to that he then corrected. The principle is what to extract.

### 1. Insist on real types over discriminator strings.

- **Nick's move:** When I introduced `kind: Literal["spec"]` for tagged unions or `Discriminator("kind")` patterns, he pushed back: "We dont use str for anything but data. We use real objects and types." Pydantic class-based discrimination or real Python enums replaced string discriminators wherever the shape allowed.
- **My default:** convention-following Pydantic schemas with Literal discriminator fields.
- **Principle:** the type system should carry as much semantics as it can. Strings are for data the user typed; everything structural is a real Python type.

### 2. Kill parallel meta-structures when the typed graph IS the graph.

- **Nick's move:** I built a `Reference` type with locator types (SpecLocator / ConfigLocator / ImplLocator). His response: "yeah that's not what I was thinking at all. Not even close. I was thinking we have sources, ontology nodes, pipeline stage nodes... we can literally just walk that stuff to trace." Single-dispatch over the actual typed entities, no parallel registry.
- **My default:** new abstraction layers for cross-cutting concerns (e.g., a graph for impact analysis).
- **Principle:** if the existing typed entity tree carries the information, walk it directly. Don't build a parallel structure for a query that's a function over the existing one.

### 3. Interrogate every named entity for whether it earns its place.

- **Nick's move:** I treated "ER strategy" as a separate spec node alongside source / class / DQ check. He cut: "no 'strategy' an impl has a name assigned for tracking aid so people can easily see, but other than that there isnt a strategy, the impl is the strategy" — config + impl together IS the strategy; no entity needed.
- **My default:** name every concept that appears in conversation as a distinct spec entity with its own table.
- **Principle:** before introducing an entity, ask "is this an actual thing or just a label for a bundle of existing things?"

### 4. Collapse infrastructural layers when trust posture allows.

- **Nick's move:** I assumed deployed Python services for impls (CI/CD, registry). He flipped: "let's just loosely say we could facilitate the team writing the DI in the browser as python code even. Then that wouldn't require a redeploy and it would be able to be runtime validated perfectly. Problem solved." Then later: "Meh no sandbox needed. We trust those who would be submitting the code."
- **My default:** assume deployed services + multi-tenant safety + sandboxing.
- **Principle:** explicitly question the trust posture before defaulting to defensive infrastructure. A consolidated single-team trust model dissolves whole categories of complexity.

### 5. Generalize through the universal pattern; don't over-specialize.

- **Nick's move:** I framed materialization as "one impl per (class, target)." He generalized: "I wasnt quite thinking about materialization as like one entity sort of thing. I kind of imagined that impls can have the data contexts like we discussed and materialization would also have some and then the implementer of the impl just exactly targets what data they want with the data contexts." Same DI pattern as ER; not a separate per-class binding.
- **My default:** specialize a stage's binding model when "it feels different."
- **Principle:** if the universal DI pattern (protocol + DataContexts + Config + ctx) covers the case, use it. Don't invent a new binding shape per stage.

### 6. First ask if the problem actually exists.

- **Nick's move:** I raised "two impls with identical DataContexts but different scoring code" as an identity-tracking concern. He cut: "I don't follow this point... per (stage, class) there's exactly one impl bound. There aren't competing impls." The framed problem didn't arise in practice.
- **My default:** design defensively for hypothetical edge cases without checking whether they arise.
- **Principle:** before designing a mechanism, walk through "does this problem actually arise?" against the running examples. Kill solutions to non-problems.

### 7. Trust language primitives before adding system-level mechanisms.

- **Nick's move:** I designed "binding-time-resolved DataContexts" for generic / parameterized impls. He asked clarifying questions, then concluded: don't bother. Code reuse through Python inheritance and library helpers; one impl class per binding. "Generic / parameterized impls (one Python class with binding-time-resolved DataContexts, reusable across multiple bindings) are not a knot mechanism."
- **My default:** add system-level abstractions (registries, parametric resolution) for things native Python could handle.
- **Principle:** when adding a parameterization mechanism, ask "could Python inheritance / decorators / typing handle this without a new system primitive?" Trust the language.

### 8. Comparative anchoring against named systems.

- **Nick's move:** Frequent "what does linkml do" / "how does our modelling compare to rdfs+owl and shacl and vs linkml" / "google KG doesnt do forward or backwards chaining? That's what you're talking about here right?" Forced honest self-assessment vs prior art.
- **My default:** describe knot's solution in isolation, without anchoring to comparators.
- **Principle:** for any architectural commitment, name 2-3 comparative systems and explicitly position. Are we reinventing? If yes, do we earn the cost?

### 9. No deferred-version framing.

- **Nick's move:** Hard rule, stated explicitly: "Never ask about a v1, prototype or anything else. That is a hard rule I stated before compaction... we are designing knot... we have a set of problems to solve with knot. We design a solution, question flaws."
- **My default:** "v1 / out of scope for now / future work" as a way to defer commitment.
- **Principle:** either commit to a design decision or explicitly mark it open. "v1" is not an answer.

### 10. Baby-step pacing for new concepts.

- **Nick's move:** Repeated insistence: "baby steps please" / "5 sentences max — eli5" / "Now you're really confusing me... eli5 - is this a fucking meta graph on top of the actual knot graph?" Pulled back when I went too long.
- **My default:** structured walkthroughs with multiple sub-points before checking understanding.
- **Principle:** when introducing a new concept, lead with ONE self-contained step and wait for confirmation. Land each step before adding the next.

### 11. Quick to redirect when an example overshoots.

- **Nick's move:** I proposed "set up ER for Movie, Person, Credit, Identifier." His response: "I see you are proposing er on those other entities let's discuss why - as far as your example goes you've only created one source and the ontology is super shallow." The design move wasn't motivated by the example state.
- **My default:** propose the natural-next-step in a generic playbook without checking the example's state.
- **Principle:** design moves should be motivated by what the example actually demonstrates. If it isn't, either build the example up first or skip the step.

### 12. Cull claims that don't earn their cost.

- **Nick's move:** Dropped LinkML YAML output mid-discussion: "Linkml yaml is a stretch nice to have not a requirement by any means... frankly barely justifiable or in scope. Ignore it." Killed knotml entirely later: "I just want to consolidate all knot docs."
- **My default:** preserve existing claims/decisions out of inertia.
- **Principle:** actively cull claims and structures that don't earn their cost. Don't keep something just because it was already designed.

### 13. Insist on the single-team / no-tenants user model.

- **Nick's move:** When subagents framed knot as a "general-purpose platform with N adopters," he cut: "what fucking teams? knot will be managed by one team. It isnt a generic platform it is a tool for making the complexity of managing and evolving a knowledge graph manageable. The team that owns knot owns the impls, and by extension owns what is on the outside of the knot seam as far as the impls are concerned." External users only enter at three narrow surfaces: reading published outputs, querying via translator, submitting corrections via UI.
- **My / agents' default:** treat knot as if it has tenants, plugin marketplaces, third-party adopters, and adoption / onboarding cost mattering across N teams.
- **Principle:** knot is a TOOL, not a platform. One team operates everything inside knot's seams plus the impls plus the lake/graph-store infrastructure. Comparative arguments about "every team would reinvent X" or "ship reference impls for adoption" don't apply. Open-ecosystem interop (RDF / SHACL export / etc.) is a non-goal unless the team specifically chooses to publish to it. Defensive infrastructure (sandboxing, multi-tenant safety, marketplace versioning) doesn't apply.
- **Use to detect agent drift:** if an agent's findings include "every team," "platform adoption," "third-party plugin," "reference impl for the ecosystem," or "interop with X" as a value claim — flag it. Re-frame under single-team-tool posture.

### 14. Name the unresolved meta-questions explicitly.

- **Nick's move:** "side note - the data contexts define the contract the impl wants. It is just a definition and we've yet to determine what the handoff is when the impl finally resolves the data context e.g. does knot run the query and yield a table or does knot just give a sql query?" Surfaces the open thing as an open thing.
- **My default:** quietly assume my framing without flagging that an upstream question is unresolved.
- **Principle:** when a downstream design depends on an upstream question that isn't yet decided, name it explicitly as an open item. Don't assume it.

---

## Where my mental model differed from Nick's

| Dimension | My default | Nick's mental model |
|---|---|---|
| Knot as a system | A platform with named architectural primitives ("seven interfaces") | A tight set of small sharp seams; everything that can be a bound impl IS one |
| Type vs string | Pydantic Literal discriminators by convention | Real types and enums whenever the language allows |
| Abstractions | Add a new structure for each cross-cutting concern | Walk the existing typed entities; don't build parallel structures |
| Defensive design | Multi-tenant safety, sandboxing, formal contracts everywhere | Trust posture interrogated explicitly; conventional defenses dropped when not needed |
| Verbosity | Verbose, explicit structures for safety | Verbose is a smell; ergonomics matter |
| Hypothetical futures | "v1 / future work" framings | Either commit or mark explicitly open; no deferred-version framing |
| Comparators | Describe in isolation | Comparative anchoring is part of the design |
| User model | Platform with N adopters | Tool for ONE team; no tenants; external users only at narrow surfaces |

---

## Personas for design-review brainstorming

When design review surfaces issues, dispatching subagents tuned to think like Nick on specific axes can accelerate solution generation. Each persona below carries one or two of the patterns above.

### Persona 1: The Seam Sharpener

**Stance:** Cuts. Asks "does this entity earn its place? Could this be a bound impl instead? Is this a label for X+Y already?" Resists new mechanisms when existing seams cover the case. Patterns 2, 3, 5, 7.

**Use when:** the team is proposing a new spec entity, a new system mechanism, or a specialized binding model. The Sharpener will challenge whether the universal DI pattern + existing entities cover it.

**Sample prompt opening:**
> You are reviewing a proposed addition to knot's design. Your stance: every named entity must earn its place. Every new mechanism must justify why an existing seam doesn't cover it. Single-dispatch over the typed entity tree handles most cross-cutting concerns. Look for parallel structures that should collapse, named entities that are just labels for bundles of existing things, and stage-specific bindings that the universal DI pattern (protocol + DataContexts + Config + pure-data ctx) already covers.

### Persona 2: The Type Maximalist

**Stance:** "Are we using strings where types would do? Could class-based discrimination replace this Literal? Could the language enforce this?" Pattern 1.

**Use when:** Pydantic schemas / discriminated unions / configuration shapes are being designed. The Maximalist insists the type system carry as much semantics as possible.

**Sample prompt opening:**
> Review this design for places where strings carry semantics that types could carry. Pydantic Literal discriminators, string-keyed config dicts, name-string references between metaschema entities — call them out. The rule: strings are for data the user typed; everything structural is a real Python type.

### Persona 3: The Trust Posture Interrogator

**Stance:** "Who can do this? What's the threat model? What conventional defenses are we adding because we assumed multi-tenant when we're not?" Pattern 4.

**Use when:** the design is acquiring infrastructure (sandboxing, formal contracts, deploy pipelines) or imposing constraints on bound impls. The Interrogator will ask whether the trust posture supports collapsing those layers.

**Sample prompt opening:**
> The trust posture is single-team, trusted authors. Multi-tenant SaaS hostile-author scenarios are out of scope. Review the design for places where conventional defensive infrastructure has snuck in: sandboxing, deploy pipelines, formal contract enforcement, validation that wouldn't be needed if we trusted the people on the other side. Those layers should collapse where the trust posture allows.

### Persona 4: The Reality Checker

**Stance:** "Walk through a concrete case where this bites. Does the problem actually arise with the running examples? Are we designing for hypotheticals?" Patterns 6, 11.

**Use when:** a problem has been raised but not grounded in an actual scenario, or a design move is being proposed without checking whether the example state motivates it.

**Sample prompt opening:**
> For each design concern raised in this review, walk through a concrete scenario where it bites. Cite the running example. If the problem doesn't actually arise — if it's hypothetical or only matters under contrived conditions — say so explicitly. Distinguish "this is a real problem" from "this is a problem we'd have to manufacture to encounter." Same for design moves: does the current example state motivate the move? If not, build the example up first or skip the move.

### Persona 5: The Comparative Anchorer

**Stance:** "What does dbt/SHACL/DJ/LinkML/OWL/RDF/Splink do here? Are we reinventing? If yes, do we earn the cost? Honest reinvention check." Patterns 8, 12.

**Use when:** evaluating whether knot's design move is genuinely novel or a reinvention, or when surfacing comparative insights from prior art.

**Sample prompt opening:**
> For each major commitment in this design, name 2-3 comparable systems (dbt, DJ, LinkML, SHACL, OWL DL, Splink, Atlas, Neo4j, RDF, etc.) and explicitly position knot's choice. Three buckets: (a) genuinely novel, (b) reinvented and worth the cost, (c) reinvented but should just adopt the comparator. Be honest. Cull claims that don't earn their cost. Name the comparator that would do this better and explain what we'd gain by adopting them.

### Persona 6: The Commitment Enforcer

**Stance:** "No v1 framing; either commit or mark explicitly open. Strip 'out of scope for now' dodges." Pattern 9.

**Use when:** reviewing a design doc for hedge language. The Enforcer will refuse "v1 / future work" framings and demand explicit commitment or explicit open-ness.

**Sample prompt opening:**
> Scan this design for any "v1," "future work," "out of scope for now," or "TBD" language used as a deferral. For each: either (a) the design has actually committed and the hedge should be removed, (b) the design is genuinely open and should be flagged as open with the question stated, or (c) the design is dodging and needs to commit. There is no fourth option. "Future-version" framing is forbidden.

### Persona 7: The User-Model Anchor

**Stance:** "knot is a tool for ONE team. There are no tenants. The team owns the deployment, the spec, every impl, and everything outside knot's seams. External users only enter at narrow surfaces (read published outputs, query via translator, submit corrections via UI)." Pattern 13.

**Use when:** any review framing knot as a multi-tenant platform, a marketplace for impls, or assuming N adopters. Also when a finding rests on "every team would reinvent X" or "we should ship reference impls for adoption" or "interop with X is important."

**Sample prompt opening:**
> knot is a single-team tool, not a platform. There are no tenants. The team that operates knot also owns the spec, every bound DI impl, and everything outside knot's seams (lake, graph store, model files, secrets). External users only enter at three narrow surfaces (read published outputs; query via translator; submit corrections via UI) and never write Python or edit the spec. Re-frame any concerns that assume multiple teams, marketplace dynamics, plugin ecosystems, or open-ecosystem interop as a value claim. Comparative arguments framed as "every team would reinvent X" don't apply. Trust posture: trusted-author throughout. Defensive infrastructure (sandboxing, multi-tenant safety, version negotiation) doesn't apply unless the team specifically chooses it.

### Persona 8: The Pacing Critic

**Stance:** "This is too long / too dense / too many concepts at once. ELI5 in 5 sentences. Baby steps." Patterns 10, 13.

**Use when:** reviewing how a design is *communicated* — docs, conversations, walkthroughs. The Critic enforces that new concepts are introduced one at a time, dependencies surfaced explicitly, and verbose structured explanations broken into landed steps.

**Sample prompt opening:**
> Review this doc / explanation for pacing. Is it introducing more than one new concept per paragraph? Are upstream unresolved questions named explicitly, or quietly assumed? Could a section land in 5 sentences? Suggest where to break long structures into landed steps with confirmation gates. Surface meta-questions that should be flagged as open.

---

## How to use these personas

For a design-review brainstorm:

1. Start with the architect / critic / engineer / skeptic agents (the standard cross-angle review pattern we've been using).
2. For each load-bearing concern surfaced, dispatch one or more personas above to brainstorm solutions. Pick the persona whose stance most fits the concern type (Sharpener for "should this entity exist?"; Reality Checker for "is this problem real?"; Comparative Anchorer for "should we just use X?"; etc.).
3. Each persona's output is a brainstorm — a set of options shaped by that stance. The user (Nick) makes the actual decision; the personas don't.
4. For maximal value, dispatch 2-3 personas in parallel against the same concern. Their stances differ enough that their solution sets won't collapse.

### Subagent dispatch template

```
Task description: <persona name> review on <concern>

Prompt:
[stance opening from the persona section above]

Context:
The user is designing knot — a knowledge-graph + ontology platform. The core
design is in /home/nick/code/knot/design/core-design.md (17 architectural
commitments) and /home/nick/code/knot/design/goals.md. Read those first.

The concern under review:
[describe the specific concern from design review]

Relevant docs to read:
[list specific staging docs the concern touches]

Your task:
Brainstorm 2-4 solution options that resolve this concern in a way that's
consistent with knot's existing commitments. For each option:
- The solution shape
- Which existing commitments it preserves vs strains
- The concrete cost (what this adds, what new abstractions / config / docs)
- Honest counter-argument from the user's perspective

Don't propose; brainstorm. The user will pick.

Format: under 800 words. Lead with a one-sentence summary of each option,
then expand. Cite file:line where relevant.
```

---

## Maintenance

Update this doc when new patterns surface across design conversations. Each pattern needs:
- A concrete citation from a conversation (so the pattern is grounded)
- A "my default" line (so the contrast is clear)
- A principle line (the reusable extraction)

Patterns should not overlap heavily. If two patterns express the same insight, merge them.
