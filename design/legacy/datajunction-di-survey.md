---
title: DataJunction DI / auth / scheduling survey
status: note
tags: [research, datajunction, reference]
project: knot
created_at: 2026-04-27T22:30:00+00:00
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# DataJunction DI / auth / scheduling survey

Reference clone at `~/code/knot/refs/datajunction/`. Survey informs
Knot's plugin / DI architecture.

## Overview

DataJunction is a uv workspace with three Python members
(`datajunction-server`, `datajunction-query`, `datajunction-clients/python`)
plus a separate React UI (`pyproject.toml` workspace declared at
`~/code/knot/refs/datajunction/pyproject.toml:11`). All of the
"interesting" backend code we care about lives under
`datajunction-server/datajunction_server/`. The FastAPI app is composed
in `api/main.py`, settings come from a single pydantic-settings
`Settings` class in `config.py`, and pluggable behaviour is layered
over that via plain ABCs + `__subclasses__()` walks, FastAPI
`Depends(...)`, and a hand-written class-level registry for SQL
transpilation.

## Dependency injection mechanism

DJ uses **stock FastAPI `Depends(...)`** for request-scoped DI plus a
handful of `@lru_cache`'d module-level factories for app-scoped
singletons. There is no `pluggy`, no `dependency-injector`, no custom
container.

Where providers live (file_path:line_number):

- `datajunction-server/datajunction_server/utils.py:69` —
  `get_settings()` (`@lru_cache`) returns the singleton `Settings`.
- `datajunction-server/datajunction_server/utils.py:202` —
  `get_session_manager()` (`@lru_cache`) returns the
  `DatabaseSessionManager` singleton; engines + sessionmakers are
  built lazily in `init_db()` at `utils.py:122`.
- `datajunction-server/datajunction_server/utils.py:227` —
  `get_session()` is the per-request async generator (writer vs reader
  routed by HTTP verb / GraphQL query detection).
- `datajunction-server/datajunction_server/utils.py:344` —
  `get_query_service_client(settings = Depends(get_settings), ...)`
  branches on `settings.query_client.type` ({"http", "snowflake",
  "bigquery"}) and lazily imports the matching client class. This is
  DJ's "config-string-to-impl" pattern for a dependency.
- `datajunction-server/datajunction_server/utils.py:529` —
  `get_current_user(request)` reads `request.state.user`, which was
  populated upstream by the auth dependency.
- `datajunction-server/datajunction_server/api/notifications.py:33` —
  `get_notifier()` returns a no-op `notify(event)` callable. The
  comment at `api/helpers.py:938` documents the pattern: "To use a
  different notify function, inject a `get_notifier` dependency", i.e.
  override the FastAPI dep at app startup.
- `datajunction-server/datajunction_server/api/helpers.py:948` —
  `get_save_history(notify = Depends(get_notifier))` composes
  notifier into a save-history callable that endpoints take as
  `save_history: Callable = Depends(get_save_history)`.
- `datajunction-server/datajunction_server/internal/access/authorization/validator.py:160` —
  `get_access_checker(auth_context = Depends(get_auth_context))`,
  building on `get_auth_context()` at
  `internal/access/authorization/context.py:137`, which itself depends
  on `get_session` and `get_current_user`.

Wiring happens in `api/main.py:88-145` (`create_app` /
`configure_app`): the FastAPI app is built with `lifespan=` (DB
session bootstrap, default attribute types, seed catalogs at
`api/main.py:68-86`), CORS + an OTel-style `DJInstrumentationMiddleware`
are added, and routers are `include_router`-ed. Authenticated routers
are created via the `SecureAPIRouter` subclass at
`internal/access/authentication/http.py:128`, which injects
`Depends(DJHTTPBearer())` as a router-level dependency.

Tests override providers using `app.dependency_overrides` (the
docstring at
`internal/access/authorization/service.py:283` calls this out
explicitly: *"can be overridden via app.dependency_overrides for
testing or custom deployments"*).

Cross-cutting: a `DialectRegistry` (`models/dialect.py:63`) is a
hand-rolled class registry for transpilation plugins, populated by
the `@dialect_plugin("name")` decorator at
`models/dialect.py:114` — this is the closest thing DJ has to an
explicit "container," and it covers exactly one concern (SQL
transpilation, see `transpilation.py:1-99`).

## Pluggable authentication

Three concerns are separately pluggable: identity (who you are),
authorization (what you can do), and group membership (transitive
roles).

**Identity / authentication.** It's hardcoded to JWT-in-cookie or
JWT-as-bearer with three built-in providers:

- `internal/access/authentication/http.py:23` — `DJHTTPBearer`
  subclasses FastAPI's `HTTPBearer`; in `__call__` it pulls
  `request.cookies.get(AUTH_COOKIE)` first, then falls back to the
  `Authorization: Bearer ...` header, decodes JWT via
  `internal/access/authentication/tokens.py`, looks up the user, and
  stuffs them onto `request.state.user`.
- `internal/access/authentication/http.py:128` — `SecureAPIRouter` is
  the deployer-facing primitive: any router built with it gets
  `Depends(DJHTTPBearer())` *iff* `settings.secret` is set. So auth is
  toggled by env var `SECRET`, not by code.
- The login routes themselves are gated by env-var presence in
  `api/main.py:166-189`: `github` router only registers if
  `GITHUB_OAUTH_CLIENT_ID` + `_SECRET` are set; same for `google`.
  `basic` is always mounted. There's no `service_account` doc but the
  router is unconditional at `api/main.py:142-143`.

**No formal "auth backend interface."** `DJHTTPBearer` is a concrete
class; there's no `AuthBackend` ABC and no entry-point or settings
hook to swap it out wholesale. The documented escape hatch
(`docs/content/0.1.0/docs/developers/authentication.md`, "How
DataJunction Stores Users") is to override `get_current_user` via
FastAPI dependency override and rely on a downstream
`get_and_update_current_user` to upsert the User row.
*Caveat:* I did not find a function literally named
`get_and_update_current_user` in the cloned tree — the docs may be
slightly out of date relative to `utils.py:529 get_current_user`.

**Authorization is genuinely pluggable**, with a clean ABC pattern:

- `internal/access/authorization/service.py:28` —
  `AuthorizationService(ABC)` with `name: str` class attribute and one
  abstract method `authorize(auth_context, requests) -> [AccessDecision]`.
- Built-ins: `RBACAuthorizationService` (line 65, `name="rbac"`) and
  `PassthroughAuthorizationService` (line 262, `name="passthrough"`).
- `service.py:278` — `get_authorization_service()` (`@lru_cache`) walks
  `AuthorizationService.__subclasses__()`, looks up
  `settings.authorization_provider`, and returns an instance. Custom
  impls register simply by being subclasses imported before first
  call. Configured via `AUTHORIZATION_PROVIDER` env var (config.py:203).
- `internal/access/authorization/validator.py:41` — `AccessChecker`
  collects `ResourceRequest`s and delegates to
  `get_authorization_service()`. Endpoints take
  `access_checker: AccessChecker = Depends(get_access_checker)`
  (e.g. `api/materializations.py:102`).

**Group membership** uses the same `__subclasses__()` discovery
pattern: `internal/access/group_membership.py:43`
(`GroupMembershipService(ABC)`, `name: str`), with
`PostgresGroupMembershipService` and `StaticGroupMembershipService`
built in, and `get_group_membership_service()` at line 185 selecting
by `settings.group_membership_provider` (env var
`GROUP_MEMBERSHIP_PROVIDER`). Doc comment at line 11-26 explicitly
shows the LDAP custom-impl story.

Deployer-facing summary: env vars only — `SECRET`,
`GITHUB_OAUTH_CLIENT_*`, `GOOGLE_OAUTH_CLIENT_*`,
`AUTHORIZATION_PROVIDER`, `GROUP_MEMBERSHIP_PROVIDER`,
`DEFAULT_ACCESS_POLICY`. Pydantic-settings nesting uses `__` delim
(`config.py:113`), e.g. `WRITER_DB__URI`, `QUERY_CLIENT__TYPE`.

## Pluggable job scheduling

Short answer: **DJ does not run jobs in-process.** Scheduling is
delegated to an external "Query Service" over HTTP, and the
materialization "job classes" are thin adapters that translate a
`Materialization` config into a payload and call
`query_service_client.materialize(...)`.

Key files:

- `datajunction-server/datajunction_server/materialization/jobs/materialization_job.py:26` —
  `MaterializationJob(abc.ABC)` with abstract `schedule(materialization,
  query_service_client) -> MaterializationInfo` and a default
  `run_backfill(...)` that just calls
  `query_service_client.run_backfill(...)`.
- `materialization/jobs/materialization_job.py:66` —
  `SparkSqlMaterializationJob(dialect=Dialect.SPARK)`. Its
  `schedule()` rewrites the AST for partition predicates, then calls
  `query_service_client.materialize(GenericMaterializationInput(...))`.
- `materialization/jobs/cube_materialization.py` defines the three
  Druid variants: `DefaultCubeMaterialization`,
  `DruidMeasuresCubeMaterializationJob`,
  `DruidMetricsCubeMaterializationJob`.
- `materialization/jobs/__init__.py:5-20` — public `__all__` listing
  the four built-in job classes.

The "registry" is `MaterializationJob.__subclasses__()` walked at
dispatch time:

- `internal/materializations.py:306` —
  `schedule_materialization_jobs(...)` builds
  `{cls.__name__: cls for cls in MaterializationJob.__subclasses__()}`,
  looks up `materialization.job` (a string), and calls
  `clazz().schedule(materialization, query_service_client, ...)`.
- Same pattern at `api/materializations.py:491` for `run_backfill`.

The user-facing job catalog is a hardcoded enum, **not**
discovery-based:

- `models/materialization.py:421` — `MaterializationJobType` pydantic
  model.
- `models/materialization.py:438` — `MaterializationJobTypeEnum` with
  exactly four values: `SPARK_SQL`, `DRUID_MEASURES_CUBE`,
  `DRUID_METRICS_CUBE`, `DRUID_CUBE`. Each carries a `job_class:
  str` that must match a `MaterializationJob` subclass `__name__`.
- `models/materialization.py:515-533` — `UpsertMaterialization.validate_job`
  rejects anything not in the enum.

So a deployer cannot add a new job type via config or entry-point
without forking — they'd need to subclass `MaterializationJob` *and*
extend `MaterializationJobTypeEnum`. The pluggability story is "Druid
vs Spark vs your-own QueryService impl on the other side of the HTTP
boundary."

How the scheduler choice surfaces:

- `config.py:144` — `celery_broker: Optional[str] = None`. The comment
  at line 142-143 says: *"Configure Celery for async requests. If not
  configured async queries will be executed using FastAPI's
  ``BackgroundTasks``."*
- `config.py:248-252` — `Settings.celery` property constructs a
  `Celery(__name__, broker=self.celery_broker)`.
- `grep -rn "celery"` on the server tree returns *only* config.py and
  pyproject deps. Celery is wired in pydantic but I did not find a
  `@celery.task` or `.delay()` call in the cloned `0.1.0` tree —
  either it's a configured-but-dead path, exercised in a sibling
  package (`datajunction-query` / `datajunction-reflection` weren't
  surveyed), or the comment describes intent rather than current
  behaviour. *Flagged as uncertainty.*
- The async-vs-background fallback the comment promises **does**
  exist for query execution: every API module that runs async work
  takes `background_tasks: BackgroundTasks` directly from FastAPI
  (`api/sql.py`, `api/data.py`, `api/system.py`,
  `api/namespaces.py`, `api/git_sync.py`, `api/deployments.py`,
  `api/graphql/main.py:183`).

Deployer-facing config: `QUERY_SERVICE` (HTTP URL) plus the
nested `QUERY_CLIENT__TYPE` / `QUERY_CLIENT__CONNECTION__*`
(config.py:41-86) decide which `QueryServiceClient` impl is built.
There is no per-deployer "swap the scheduler" knob — the scheduler
lives in whatever service answers `/materialize`.

## What's directly applicable to Knot

- **ABC + `name` + `__subclasses__()` discovery** is dead simple and
  works well for small, finite plugin axes (auth, authz, group
  membership, materialization jobs). No setuptools entry-points
  ceremony, no DI container library. We can lift this verbatim for,
  say, graph-store backends and DQ engines if we keep the plugin set
  small.
- **`pydantic-settings` with `env_nested_delimiter="__"`** for typed,
  nested config (e.g. `QUERY_CLIENT__TYPE`,
  `QUERY_CLIENT__CONNECTION__PROJECT`) is a clean way to express
  "select an impl + pass it args" without YAML.
- **A `SecureAPIRouter` subclass** that conditionally adds
  `Depends(auth)` keyed off `settings.secret` lets us ship a useful
  dev mode (no auth) and prod mode (JWT) from one codebase.
- **Pre-loaded `AuthContext`** (one query for direct + group role
  assignments via `selectinload`, then sync in-memory authorization
  decisions) is a nice perf pattern — avoids N+1 inside per-resource
  authorization checks.
- **`AccessChecker` as a dependency that endpoints pass to internal
  helpers** so the helper can collect requests and check them at the
  end — cleaner than scattering `if can(user, X)` everywhere.
- **`@lru_cache`'d module-level factories for app-scoped singletons**
  (settings, session manager, services). Plain Python, easy to mock
  via `app.dependency_overrides` or `cache_clear()` in tests.
- **The "config-string switches lazy-imported impl"** pattern in
  `_create_configured_query_client` (utils.py:370) is a good template
  for optional vendor deps — failure surface is "raise ValueError with
  install hint."

## What we'd do differently

- DJ's `MaterializationJobTypeEnum` *plus* `MaterializationJob`
  subclasses is **two registries that must agree by string match**.
  That's a footgun. Knot should pick one — either the enum is the
  registry (enum members carry the class) or the ABC discovery is
  authoritative.
- DJ has **no formal pluggable identity / authentication backend**.
  `DJHTTPBearer` is hardcoded; the documented extension story is
  `app.dependency_overrides` of `get_current_user`. For a
  Netflix-internal Knot we likely want a real `IdentityProvider` ABC
  with the same `name` + `__subclasses__()` shape used for authz.
- **No setuptools entry-points for plugins.** DJ relies on "import the
  subclass before first call." Fine for a monolith, painful for
  out-of-tree plugins. If Knot wants third-party plugins (likely no,
  given internal-only), we'd add `[project.entry-points.'knot.plugins']`.
  DJ uses entry-points only once and only for Superset interop
  (`datajunction-server/pyproject.toml:120-121`).
- **Celery is half-wired** (in deps + config but I found no
  `task`/`delay` calls in the surveyed tree). Knot should commit:
  either Celery is a real path or it isn't. A clean
  `JobScheduler` ABC with `InProcessScheduler` (FastAPI BackgroundTasks)
  / `CeleryScheduler` / `MaestroScheduler` impls would be honest.
- DJ's `@lru_cache`'d service-getters tie test isolation to
  `cache_clear()` + `app.dependency_overrides`. That's livable but
  brittle — Knot may want explicit per-app state on `app.state` for
  better test ergonomics.
- **Settings is one giant class** (config.py:108-278) with
  ~40 fields covering DB, cache, Celery, every OAuth provider, GitHub
  app config, RBAC tuning, etc. Knot should split by concern and
  compose, otherwise the settings file becomes the bottleneck for
  every new plugin axis.
- **Authorization service is sync**, group-membership service is
  async. DJ explicitly notes (service.py:79) the sync choice depends
  on AuthContext having pre-loaded all data. That's fine but should
  be a deliberate design choice for Knot, not an accident.

## Open questions / gaps in the survey

- **Is Celery actually used at runtime?** I only found it in
  `config.py` and `pyproject.toml`. `datajunction-query` and
  `datajunction-reflection` were not surveyed (they're outside the uv
  workspace `members` list and may not even be live in 0.1.0). If
  there's a Celery worker, it lives there or in
  `datajunction-server/scripts/`.
- **What's the actual production scheduler at Netflix?** The
  docs/deploying-dj/materialization-service.md and
  the-components-of-a-dj-deployment.md files are stubs (`draft: true`,
  one-line bodies). The whole "how do I plug Maestro / Airflow /
  whatever in" question is answered only implicitly by "implement a
  QueryService that speaks DJ's HTTP contract."
- **The `get_and_update_current_user` referenced in the auth doc** —
  I didn't locate it; either renamed or dropped. The current
  `utils.get_current_user` just reads `request.state.user`.
- **The `service_clients.QueryServiceClient` interface** — I saw it
  imported but didn't read the full surface. That contract *is* the
  scheduler-pluggability seam, so it deserves a follow-up read if/when
  we get serious about job orchestration in Knot.
- **GraphQL DI** — `api/graphql/main.py:207` puts
  `background_tasks` into the strawberry context dict. I didn't trace
  how `Depends`-style dependencies flow into resolvers; relevant only
  if Knot exposes GraphQL.
- **Reflection / lineage** — explicitly out of scope per the
  task brief, flagging that any "scheduled refresh of metadata" lives
  in `datajunction-reflection`, not the server.
