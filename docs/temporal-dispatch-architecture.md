# Temporal Dispatch Architecture: Workers, Not APIs

The architecture for how the main service dispatches work to supporting services. The thesis: **use Temporal task queues as the dispatch mechanism, not REST/gRPC APIs.**

The setup: **the main service is Java + Spring Boot. The Temporal workflows and workers are Python.** The Java side uses the Temporal Java SDK *only* as a client — it starts workflows, sends signals, queries status. All workflow definitions and activity implementations live in the Python side.

---

## Context

The main service is the REST API (Java/Spring Boot). It receives writes (a source pushing bindings, a curator submitting corrections) and reads. When a write lands, it needs to trigger downstream work:

- **Matching** (ER): decide whether the new binding refers to an existing canonical entity or a new one
- **Embedding**: compute vector embeddings for text slots (titles, names, synopses)
- **Genre/subgenre prediction**: ML classifiers run on the new entity
- **Quality scoring**: compute trust signals from cross-source agreement
- **Future ML workflows**: thumbnail tagging, content advisories, mood classification, anything else

None of this should happen synchronously in the request path. The request returns once the binding is written; the downstream work runs asynchronously.

The question is **how** the main service dispatches that work.

---

## Two Models

### Model 1: Supporting Services as HTTP/gRPC APIs

Each supporting capability is its own microservice with an API.

```
[Main Service] --HTTP--> [Matching Service]
              --HTTP--> [Embedding Service]
              --HTTP--> [Genre Service]
              --HTTP--> [...]
```

The main service POSTs to each, gets a response (or a job ID for async), reports back to the caller or stores the result.

### Model 2: Supporting Services as Temporal Workers

The main service starts a Temporal workflow. The workflow orchestrates child workflows / activities. Each supporting "service" is just a Temporal worker polling its own task queue. There are no HTTP servers downstream of the main service.

```
[Main Service]
     |
     | start_workflow(IngestPipeline)
     v
[Temporal Server] ----> task queues
     ^
     | poll
     |
[Matching Workers]   [Embedding Workers]   [Genre Workers]   [...]
```

---

## Why Model 2 Wins for This Workload

### 1. The main service stays thin

In Model 1, the main service has to know about every downstream service: URLs, health checks, retry logic, timeout handling, partial-failure recovery, idempotency keys per call. Every new ML capability added means more orchestration code in the Java service.

In Model 2, the Java main service knows about Temporal and nothing else. It uses the Temporal Java SDK as a *client only* — `WorkflowClient.newWorkflowStub(...).start(...)`. The workflow definition lives in Python; Java just dispatches it by name.

```java
@RestController
@RequestMapping("/api/v1/bindings")
public class BindingController {

    private final BindingRepository repository;
    private final WorkflowClient temporalClient;

    public BindingController(BindingRepository repository, WorkflowClient temporalClient) {
        this.repository = repository;
        this.temporalClient = temporalClient;
    }

    @PostMapping("/{className}/{source}")
    public ResponseEntity<DispatchResponse> writeBinding(
            @PathVariable String className,
            @PathVariable String source,
            @Valid @RequestBody BindingRequest request) {

        // 1. Synchronous write to the bindings table
        repository.save(Binding.from(className, source, request));

        // 2. Dispatch the Python workflow via task queue
        IngestPipelineStub workflow = temporalClient.newWorkflowStub(
            IngestPipelineStub.class,
            WorkflowOptions.newBuilder()
                .setTaskQueue("ingest-orchestrator")  // Python workers poll this queue
                .setWorkflowId("ingest-" + className + "-" + source + "-" + request.identifier())
                .build()
        );
        WorkflowClient.start(workflow::run,
            new IngestInput(className, source, request.identifier()));

        return ResponseEntity.accepted()
            .body(new DispatchResponse(request.identifier(), "queued"));
    }
}
```

Note the `IngestPipelineStub` interface: it's a Java interface that mirrors the Python workflow's signature. The Java client uses it to start the workflow by name — Temporal routes the task to a Python worker polling `ingest-orchestrator`, and the Python implementation runs. The Java side never sees the workflow code; the SDK just needs the interface to type the stub.

```java
@WorkflowInterface
public interface IngestPipelineStub {
    @WorkflowMethod
    void run(IngestInput input);
}
```

That's the entire dispatch logic. New ML capability? Add an activity to the Python workflow. The Java service doesn't change.

### 2. Durable execution replaces operational glue

In Model 1, if the embedding service is down, the main service has to handle: retry with backoff, dead-letter the work, alert if retries exhaust, recover state on main-service crash mid-call. Every team writes this glue, each one slightly differently, each with its own bugs.

In Model 2, the workflow IS the glue. The activity that calls the embedding worker has a retry policy declaratively defined. Temporal handles the backoff, the persistence of "this activity is in flight," the recovery when a worker crashes. If the embedding workers are all down for an hour, the workflow waits. When workers come back, the activity runs. No state lost, no main-service involvement.

### 3. Workers vs APIs: simpler service shape

A Temporal worker is a process that polls a task queue and runs functions. It has no HTTP server, no load balancer, no health check endpoint, no rate limiter (Temporal handles it). It scales horizontally by adding more workers; the only contract is "implement these activity functions."

An HTTP service is a server with all of the above. More moving parts. More to misconfigure.

For pure backend compute (no human or external clients), the worker shape is genuinely simpler.

### 4. Task queue depth is observable backpressure

In Model 1, you find out the embedding service is overwhelmed when latency spikes and 5xx errors appear. You build dashboards, alert thresholds, circuit breakers.

In Model 2, you watch the queue depth metric. Backpressure is a number you can graph. Workers are slow? Queue grows. Add workers. The system self-regulates because workers pull work, not push.

### 5. Resource isolation maps to task queue routing

Embedding workers want GPUs. Matching workers want lots of RAM. Genre prediction wants both. With Model 1, each is a separate microservice with its own deployment, container, infra config.

With Model 2, they're all "workers polling Temporal." The task queue is the routing mechanism:

```python
# Activity that needs GPU
await workflow.execute_activity(
    embed_text, payload,
    task_queue="gpu-embedding",     # specifically GPU workers
    start_to_close_timeout=timedelta(minutes=5),
)

# Activity that needs RAM
await workflow.execute_activity(
    match_candidates, payload,
    task_queue="memory-matching",   # specifically high-memory workers
    start_to_close_timeout=timedelta(minutes=10),
)
```

GPU workers and CPU workers run the same code patterns, just register different activity sets and poll different queues.

### 6. Versioning is built in

In Model 1, supporting service version skew is your problem: API v1 vs v2, header negotiation, deprecation timelines, multi-version routing.

In Model 2, Temporal's worker Build IDs handle this. You deploy a new embedding model, you stamp it with a new Build ID, you route new workflows to the new Build ID while old workflows finish on the old Build ID. The mechanism is the same for every supporting capability.

---

## The Architecture

### Orchestrator workflow (Python)

The workflow definition and all activities live in the Python codebase. The Java service never imports any of this — it only knows the workflow ID convention and the input shape.

```python
@workflow.defn
class IngestPipeline:
    @workflow.run
    async def run(self, input: IngestInput) -> IngestResult:
        # Embed new bindings (text slots)
        embeddings = await workflow.execute_activity(
            embed_text_slots,
            EmbedInput(class_name=input.class_name, source=input.source, identifier=input.identifier),
            task_queue="gpu-embedding",
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )

        # Match: determine canonical_id (existing or new)
        match_result = await workflow.execute_child_workflow(
            MatchingWorkflow.run,
            MatchInput(class_name=input.class_name, binding=input.identifier, embeddings=embeddings),
            id=f"match-{input.class_name}-{input.identifier}",
            task_queue="matching-orchestrator",
        )

        # Run ML enrichments in parallel
        ml_tasks = [
            workflow.execute_child_workflow(GenrePrediction.run, ..., task_queue="ml-cpu"),
            workflow.execute_child_workflow(MoodClassification.run, ..., task_queue="ml-gpu"),
            # ... add new ones here as the team adds capabilities
        ]
        ml_results = await asyncio.gather(*ml_tasks, return_exceptions=True)

        # Atomic commit at the orchestrator (resolves cross-batch conflicts)
        await workflow.execute_activity(
            commit_staged_results,
            CommitInput(match=match_result, enrichments=ml_results),
            task_queue="db-writer",
            start_to_close_timeout=timedelta(minutes=2),
        )

        return IngestResult(canonical_id=match_result.canonical_id, ml=ml_results)
```

### Matching as a child workflow

Per earlier discussion (and the "matching workflow pattern" companion doc), matching is a child workflow with one activity per funnel stage: embed → block (candidate retrieval) → rank (cross-encoder or XGBoost) → decide (mint or stamp). Each stage has its own retry policy and timeout. The child workflow's contract is "given a binding, return a staged proposal." It does not write canonical state directly.

### Staging and atomic commit

Child workflows write proposed changes to a staging area (a workflow-scoped table or a shared staging schema with workflow_id keys). The orchestrator collects proposals, resolves cross-batch conflicts (two batches both proposing to mint the same canonical_id), then commits everything in one transaction. This prevents the "batch A mints X, batch B mints Y, but X and Y are actually the same entity" race.

### Triggering from the Java service

The Java REST controller has exactly one Temporal client call per ingest: `WorkflowClient.start(workflow::run, input)`. The workflow ID is meaningful (`ingest-{class}-{source}-{identifier}`), which gives workflow-ID-based deduplication for free (set `WorkflowIdReusePolicy.REJECT_DUPLICATE` to short-circuit duplicate ingests).

For batched ingest (a curator-submitted batch of corrections, or a coordinated multi-binding write):

```java
@PostMapping("/batch/{className}/{source}")
public ResponseEntity<DispatchResponse> writeBatch(
        @PathVariable String className,
        @PathVariable String source,
        @Valid @RequestBody BatchRequest request) {

    String batchId = request.batchId();
    repository.saveAll(request.bindings().stream()
        .map(b -> Binding.from(className, source, b)).toList());

    BatchIngestPipelineStub workflow = temporalClient.newWorkflowStub(
        BatchIngestPipelineStub.class,
        WorkflowOptions.newBuilder()
            .setTaskQueue("ingest-orchestrator")
            .setWorkflowId("batch-" + className + "-" + source + "-" + batchId)
            .build()
    );
    WorkflowClient.start(workflow::run,
        new BatchInput(className, source, batchId));

    return ResponseEntity.accepted()
        .body(new DispatchResponse(batchId, "queued"));
}
```

The Python batch workflow fans out to per-entity ingest pipelines as child workflows, then merges results — same pattern as the single-entity case, just with one level of fan-out.

### Cross-language workflow contracts

The Java service and the Python workflows agree on:
1. **Workflow names** (the class name, e.g. `IngestPipeline`)
2. **Input/output types** (serialized as JSON by default)
3. **Task queue names** (where Python workers poll)
4. **Workflow ID conventions** (so the Java service can later signal or query the workflow)

The Java side declares an interface (`@WorkflowInterface`) that mirrors the Python workflow's shape. The interface is the contract. The Python implementation lives entirely in the Python codebase. The Temporal server doesn't care which language implemented the workflow — it dispatches the task to whoever is polling the queue.

The shared types (`IngestInput`, `BatchInput`) need to serialize compatibly between Java and Python. The simplest approach: Java records → Pydantic models with matching field names. Both serialize to the same JSON. For more complex cases, codegen both from a single source (Protobuf, JSON Schema, or the thin modeling layer described in the postgres modeling doc).

---

## What Stays as APIs

The Java main service is still a REST API — that's its job. External clients hit it. What changes is what happens *behind* the API.

- The Java REST API handles authentication, validation, and the initial DB write (via JPA).
- It dispatches to Temporal using the Java SDK as a client.
- All supporting capabilities (matching, embedding, ML, scoring) are Python workers on Temporal task queues.
- The Java service queries Temporal for workflow status when a caller wants progress (`GET /ingest/{id}/status` → `workflow.getStatus()` query method on the Python workflow).
- The Java service signals a Python workflow when a caller submits new info mid-pipeline.

The result: the Java service stays small, focused on the REST boundary, JPA persistence, and Temporal client calls. All the orchestration complexity lives in Python workflows, not in Java glue code. The Java codebase has no embedding logic, no matching logic, no ML model loading — those are all Python concerns.

---

## What This Buys Us Operationally

| Concern | Model 1 (APIs) | Model 2 (Temporal workers) |
|---|---|---|
| Retry on failure | Per-service custom code | Declarative retry policy |
| State on crash | Lost unless persisted | Recovered from event history |
| Backpressure | 5xx + dashboards | Queue depth metric |
| Versioning | API versioning (headers, paths) | Worker Build IDs |
| Discovery | Service registry / DNS / hardcoded | Task queue name |
| Health checks | Per-service endpoint | Worker polling cadence |
| Resource isolation | Separate services + deploys | Task queue routing |
| Adding a new capability | New service + deploy + main service integration | New worker on a new task queue + add to orchestrator |
| Observability | Logs + traces + metrics per service | One audit log per workflow (the event history) |

---

## What This Does NOT Do

This pattern is for **dispatching async work from the main service to supporting capabilities the team owns**. It is not for:

- **External integrations.** Calling Slack, PagerDuty, GitHub, etc. — those have HTTP APIs and you call them from inside an activity. The activity is the boundary.
- **Synchronous request/response.** If a caller needs an answer before the response returns, you can't dispatch to a workflow and wait — well, you can with `execute_workflow` (the convenience method that waits), but it defeats the durability story. Use workflows for async work; use direct in-process logic for sync work.
- **High-throughput per-message processing.** Temporal isn't a message broker. If you have 100K events/sec from Kafka and you want to process each one with millisecond latency, Temporal is the wrong tool. Use Kafka consumers or Flink for that, and use Temporal to orchestrate the bigger pipeline they live in.
- **Batch vendor dumps as the main ingest path.** Per earlier scope: batch ingest from vendor dumps stays outside this pattern (or has its own dedicated batch ingest workflows). The pattern here is for pipelines triggered *at ingest time* when bindings are written through the REST API.

---

## Recommendation

- **Main service: Java + Spring Boot.** REST API, JPA persistence, Temporal Java SDK as a client only (no workflows or activities in the Java codebase).
- **Workflows and workers: Python.** All orchestration, matching, embedding, and ML enrichments live in Python. Workers poll Temporal task queues; no HTTP servers downstream of the Java service.
- **Cross-language contract: workflow interfaces.** Java declares `@WorkflowInterface` stubs that mirror the Python workflow signatures. Shared input/output types serialize compatibly (Java records ↔ Pydantic models with matching fields, or codegen both from one source).
- **One orchestrator workflow per ingest event** (`IngestPipeline`), child workflows for distinct capabilities (matching, ML enrichments), activities for individual stages within a child workflow.
- **Task queues route by resource type** (GPU embedding, CPU matching, DB writer).
- **Staging area + atomic commit at the orchestrator** resolves cross-batch races (e.g., two batches both proposing to mint the same canonical_id).

The team doesn't build microservice APIs in Python for matching, embedding, or genre prediction. They build Python Temporal workers. The Java service starts the workflows; the Python workers execute them. The Java side stays focused on the REST boundary, the Python side stays focused on the orchestration and ML compute.
