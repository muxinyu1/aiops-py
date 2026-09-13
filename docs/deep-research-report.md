# State of the Art for Solving Multi-Step Preconditions in Web/API Testing

## Executive synthesis and literature verification

The literature splits the “multi-step prerequisite” problem into three rather different problems that are often conflated:

1. **Protocol/resource state:** an API call needs a value or resource produced by an earlier call. RESTler, MOREST, resource-aware EvoMaster, DeepREST, KAT, AutoRestTest, and the very recent APIPilot all attack this problem in different ways. RESTler’s producer–consumer grammar is the canonical starting point; MOREST adds execution-time model correction; DeepREST learns hidden operation ordering from interaction; recent LLM systems add semantic dependency inference. citeturn13search0turn19search1turn21view1
2. **Persistent environment state:** the desired path depends on what is already in a database. Here the strongest and closest line of work is **EvoMaster’s SQL handling**, not the black-box RESTler lineage. EvoMaster instruments SQL queries, creates search objectives for unsatisfied `SELECT`s, evolves database initialization data, and can directly insert records into relational databases when constructing them through HTTP would be impossible or too costly. This is an important collision with your proposed DB seeding idea. citeturn18view0turn18view1
3. **Internal semantic state:** the desired path requires an internal exception, a strict branch predicate, a library behavior, or another condition not naturally represented as an API resource. Traditional crash reproduction, especially EvoCrash, works backward from a crash/stack trace, while the newest REST work MioHint uses source-level data dependencies plus an LLM to produce target-specific HTTP mutations. **I found no established REST-testing system that makes “cause exception type `E` so execution enters this particular `catch(E)` containing target sink `S`” a first-class prerequisite-solving problem.** citeturn14view3turn15view1turn20search11

This distinction matters for novelty. **“Automatically insert MySQL seed data before an HTTP test” is already prior art. “Use an LLM to read server source and generate target-specific HTTP values” is also now prior art.** The combination that still appears underexplored is: *sink-directed source analysis → infer a concrete persistent-state predicate or exception predicate → synthesize an explicit setup action (SQL tuple or HTTP exception-triggering request) → execute and validate that this setup makes a designated internal sink reachable*. citeturn18view0turn14view3

There are several corrections to the seed list in the question. **AutoRestTest is an ICSE 2025 work, not ISSTA 2024**; its research paper is *A Multi-Agent Approach for REST API Testing with Semantic Graphs and LLM-Driven Inputs*, with a separate tool paper/preprint, arXiv:2501.08600. citeturn21view1turn21view2 **MioHint**, initially arXiv:2504.05738 in 2025, is now an **ICSE 2026 Research Track** paper. citeturn13search19turn20search6 The testing paper called **RESTGPT** is the ICSE-NIER 2024 work *Leveraging Large Language Models to Improve REST API Testing*, DOI `10.1145/3639476.3639769`, arXiv:2312.00894; it should not be confused with the unrelated LLM API-agent paper also named RestGPT. citeturn19search2turn19search18

I could **not** verify an “Eva, ICSE 2022” paper matching the described database-feedback technique. The verified database-feedback lineage is Arcuri and Galeotti’s GECCO 2019 / TOSEM 2020 SQL work and its EvoMaster extensions; the prominent ICSE 2022 stateful-REST paper is MOREST. citeturn16search2turn16search1turn13search0 Likewise, exact-name searches did not establish **MoreLLA, MOAT, NestRA, or Spearmint** as peer-reviewed REST-API-testing papers matching the requested problem, so I have not attributed techniques or results to them. “Minerva” and “Titan” are overloaded names in adjacent fuzzing areas; they are not needed to support the conclusions below.

## Stateful API dependencies and multi-call prerequisite solving

The main progression in RQ1 is from **syntactic dependency inference → dynamic dependency learning → semantic/LLM inference → execution-validated semantic inference**.

RESTler established the now-standard abstraction: treat outputs of earlier requests as **producers** and parameters of later requests as **consumers**. A Swagger/OpenAPI specification is compiled into a stateful fuzzing grammar, and request sequences are generated only when earlier calls can provide the values required by later ones. This turns a flat endpoint fuzzer into a protocol-state fuzzer. RESTler is black-box with respect to application source code; its key knowledge source is the API specification and values obtained dynamically from responses. DOI `10.1109/ICSE.2019.00083`. citeturn0search0turn2search22turn17view1

MOREST observed that a fixed dependency model inferred from OpenAPI is often wrong or incomplete. It constructs a **RESTful-service Property Graph (RPG)** and updates that graph using execution feedback, using the model to generate meaningful operation sequences. On six real projects it reported 26.16–103.24% more covered lines and 40.64–215.94% more detected bugs than compared techniques, finding 44 bugs, including 13 not found by the compared approaches. ICSE 2022, DOI `10.1145/3510003.3510133`, arXiv:2204.12148. citeturn13search0

A separate EvoMaster line models REST operations in terms of **resources**. A test individual can contain resource-handling fragments such as create-then-read, with values bound across operations; evolutionary operators add, remove, reorder, or replace resource fragments. Crucially, the later version can infer relations between resources not only from URI/OpenAPI structure but also from **which database objects the endpoints access**. This is more relevant to your scenario than RESTler because it begins to bridge HTTP-level dependency graphs and server-internal persistent state. DOI `10.1007/s10664-020-09937-1`. citeturn18view2turn21view3turn18view0

DeepREST takes a different black-box route. OpenAPI reveals only some producer–consumer constraints; DeepREST uses curiosity-driven deep reinforcement learning to learn **implicit operation ordering** from interaction. The agent learns which operations should be invoked first to bring the system into a state in which later calls work, while successful interactions feed its input-generation policy. This is arguably the cleanest non-LLM answer to “the prerequisite sequence is hidden.” It appeared at ASE 2024, DOI `10.1145/3691620.3695511`, arXiv:2408.08594. citeturn19search1turn19search9turn20search1

The current frontier is APIPilot, posted as arXiv:2608.17546v2 on August 20, 2026. It explicitly treats an inferred dependency as a **hypothesis** rather than truth: structural heuristics and an LLM propose producer–consumer mappings, real API calls validate them, successful mappings enter a dependency graph, and bounded top-\(k\) graph traversal constructs workflows. Responses continuously update resource pools and constraints and remove bad mappings. This separation—**LLM for semantic edge proposal, execution for truth, graph algorithm for sequence construction**—is a significant maturation over “ask an LLM to output a workflow.” In its 16-service evaluation it reports 92.3% operation coverage, 88.1% workflow execution success, up to 58.6% code coverage, and 197 unique 5xx/specification-execution mismatches. As of September 6, 2026, the source I could verify is still a preprint rather than a peer-reviewed conference version. citeturn21view1

### Comparison of the principal systems

| Tool / paper | Dependency modeling | How prerequisite state is obtained | White/black box | LLM use | Evidence and important limitation |
|---|---|---|---|---|---|
| **RESTler**, ICSE 2019, DOI `10.1109/ICSE.2019.00083` | Producer–consumer dependencies compiled into a stateful request grammar | Earlier HTTP calls create resources; dynamic response values such as IDs are propagated into later consumers | Black-box, OpenAPI/Swagger | None | Foundational solution for resource-ID chains. It does **not infer arbitrary server-side DB predicates or exception prerequisites from source**. citeturn0search0turn2search22 |
| **MOREST**, ICSE 2022, DOI `10.1145/3510003.3510133`, arXiv:2204.12148 | Dynamically updated RESTful-service Property Graph | API calls establish state; execution feedback corrects/refines the graph | Black-box | None in original work | Six real projects; +26.16–103.24% line coverage and 44 bugs reported. Still learns externally observable API relations, not arbitrary DB tuples. citeturn13search0 |
| **EvoMaster resource/dependency generation**, EMSE 2021, DOI `10.1007/s10664-020-09937-1` | Hierarchical resource/action/gene representation; inferred resource-dependency probabilities | POST/PUT-style setup sequences; values bound across actions; relations can be learned during search | Primarily white-box in the strongest configuration | None | Explicitly evolves multi-resource sequences; search must still discover complex prerequisites. citeturn18view2turn21view3 |
| **EvoMaster SQL handling**, TOSEM 2020, DOI `10.1145/3391533`; SSBSE 2021 extension DOI `10.1007/978-3-030-88106-1_8` | SQL-query objectives plus HTTP/resource search; DB initialization is part of the test genotype | Monitor executed SQL, use query-distance feedback, generate/evolve tuples, and **directly insert them into SQL DBs** when necessary | White-box / runtime-instrumented | None | This is the strongest collision with `db_state`. Direct SQL/MySQL seeding itself is not novel. citeturn16search1turn16search0turn18view0 |
| **DeepREST**, ASE 2024, DOI `10.1145/3691620.3695511`, arXiv:2408.08594 | DRL policy over operations and input decisions; learns hidden ordering | Successful executions teach which calls put the SUT into a testable state | Black-box | None | Learns implicit workflow constraints without source. It cannot directly create inaccessible DB state. citeturn19search1turn20search1 |
| **RESTGPT**, ICSE-NIER 2024, DOI `10.1145/3639476.3639769`, arXiv:2312.00894 | Extracts machine-readable constraints from natural-language OAS descriptions | Produces example parameter values and constraints that another tester can use | Black-box/specification assistant | LLM parses descriptions and generates examples/rules | **Not principally a sequence planner and not setup-code generation**; its role is specification enhancement. citeturn19search2 |
| **KAT**, ICST 2024, arXiv:2407.10227 | Operation Dependency Graph plus inter-parameter constraints | Predecessor operation results feed downstream tests; LLM-generated scripts/data embody the dependencies | Black-box/OpenAPI | GPT participates in dependency inference, scripts, constraint-validation code, test cases and test data | Twelve services; broader LLM role than RESTGPT, but inferred dependencies are based on API-level information, not server source/DB semantics. citeturn14view1turn21view0 |
| **AutoRestTest**, ICSE 2025 | Semantic Property Dependency Graph; Q-learning/MARL agents for operation, dependency, parameter, value, header choices | API sequences and dynamic responses establish state | Black-box/OpenAPI | LLM generates realistic values/headers; **sequence search is principally graph + RL**, not free-form LLM planning | SPDG edges use semantic similarity; APIPilot later shows that such similarity edges can be spurious. citeturn21view2turn21view1 |
| **LlamaRestTest**, FSE 2025, DOI `10.1145/3715737`, arXiv:2501.08598 | Explicit **inter-parameter** dependency rules, integrated with ARAT-RL | Server error messages refine parameter sets/values at runtime | Black-box | Fine-tuned Llama3-8B models identify parameter dependencies and generate values | On 12 services, 8-bit FT version reached 55.8% method / 28.3% branch / 55.3% line coverage; removing its dependency model reduced branch coverage by 21.9%. It primarily handles **within-operation parameters**, not persistent DB setup. citeturn14view2 |
| **MioHint**, ICSE 2026, arXiv:2504.05738 | Cross-file, cross-procedure, statement-level def-use slice from request to hard target | Starts from an EvoMaster test and asks the LLM for a target-specific request mutation | White-box, Java evaluation | GPT-4o reads a minimized source slice and emits mutation hints | +4.95 percentage-point average line coverage; >57% hard-target coverage vs <10% baseline. Crucially, DB/configuration-related targets were excluded because request mutation could not address them. citeturn14view3turn15view0turn15view1 |
| **APIPilot**, arXiv:2608.17546v2, 2026 preprint | LLM/structural producer–consumer hypotheses → execution validation → dependency graph → bounded top-\(k\) workflows | Runtime-created resources are pooled/reused; responses refine constraints and prune invalid dependencies | Black-box/OpenAPI | LLM proposes semantic dependencies; **does not directly generate final sequence as authority** | 16 services; 92.3% operation coverage and 88.1% workflow success. No source/DB/exception-precondition reasoning. citeturn21view1 |

One cross-cutting gap is **authentication bootstrap**. The dependency literature overwhelmingly treats resource IDs, parameters and endpoint ordering as the interesting state variables. Authentication credentials/tokens are generally an environmental/test configuration concern rather than something these algorithms discover by reasoning backward from a target. In other words, “obtain a valid token/session first” is supported operationally by many tools, but **automatic synthesis of a login/MFA/session-establishment workflow is not treated with the same rigor as producer–consumer resource dependencies** in the core papers above. That makes authentication a useful separate prerequisite category in any sink-reachability model rather than assuming it is solved by RESTler-style dependencies. The latest APIPilot examples, for instance, reason about resource IDs under operations described as belonging to an “authenticated user,” while its research contribution begins downstream at API producer–consumer mappings. citeturn21view1

## Database state as a prerequisite

### What the established SOTA actually does

For your `db_state` category, the most important result of this review is that **the strongest prior work is substantially more capable than ordinary stateful REST fuzzers**.

Arcuri and Galeotti first introduced SQL-aware search-based system testing at GECCO 2019, DOI `10.1145/3321707.3321732`, and developed it into *Handling SQL Databases in Automated System Test Generation*, TOSEM 2020, DOI `10.1145/3391533`. The central observation is almost exactly your problem: an HTTP request can reach a database query, and execution may diverge depending on whether a `SELECT` returns rows satisfying a potentially complex predicate. citeturn16search2turn16search1

EvoMaster therefore instruments database interaction and introduces a **secondary testing objective for SQL queries that return no rows**. Its instrumentation calculates a distance-like score estimating how close the current DB contents are to making the query return a nonempty result. That provides the evolutionary search with a gradient where ordinary HTTP branch coverage provides none. citeturn18view1

Even more importantly, EvoMaster does not require every desired database state to be produced through public APIs. When the application is read-only, when another microservice normally populates the table, or when the REST-call sequence needed to construct the state is too complex, it can **synthesize and directly insert database records**. Database initialization actions are evolved together with query parameters, JSON fields, and other HTTP input genes. The tool report explicitly lists PostgreSQL/MySQL-style relational databases as motivating cases. citeturn18view0

The SSBSE 2021 paper *Enhancing Resource-Based Test Case Generation for RESTful APIs with SQL Handling*, DOI `10.1007/978-3-030-88106-1_8`, goes one step further by integrating the DB information with resource-aware HTTP test generation. EvoMaster can infer that endpoints/resources are related based on the database entities they access, not merely from similarly named URL paths. citeturn16search0turn18view0

So for the three subquestions in RQ2:

| DB-state question | Best verified answer in prior work |
|---|---|
| **How is the required seed inferred?** | Primarily through **runtime observation of executed SQL queries and their predicates**, with search heuristics estimating closeness to satisfying an empty-result query—not through an LLM interpreting business logic. citeturn18view1 |
| **API precreation or direct SQL?** | Both. Resource-aware tests can prepend POST/PUT-style setup actions, but EvoMaster can bypass HTTP and directly insert DB data when the API cannot produce it or the sequence is too difficult. citeturn18view0turn18view2 |
| **How is generation guided?** | Search-based evolution of database initialization genes plus SQL-specific fitness/query-distance feedback. citeturn18view0turn18view1 |

There has also been a recent extension to **MongoDB/NoSQL**, arXiv:2507.20848. The authors emphasize that simply porting relational SQL machinery is insufficient because MongoDB lacks a rigid schema; they add Mongo-specific runtime heuristics and direct document generation. Across six REST APIs they report code-coverage increases of up to 18% over existing white-box configurations. This confirms that DB state remains an active research problem rather than a solved implementation detail. citeturn18view1

### Where the DB-state gap remains

The crucial distinction for your idea is **how the seed condition is obtained**.

EvoMaster's approach is fundamentally **dynamic and optimization-based**:

\[
\text{HTTP execution}
\rightarrow
\text{observe SQL query}
\rightarrow
\text{compute SQL fitness}
\rightarrow
\text{evolve DB tuple}
\rightarrow
\text{re-run}
\]

Your proposed mechanism sounds fundamentally **target-directed and semantic**:

\[
\text{target sink}
\rightarrow
\text{backward source slice}
\rightarrow
\text{infer branch/DB semantics}
\rightarrow
\text{derive concrete relational tuple}
\rightarrow
\text{direct seed}
\rightarrow
\text{send HTTP chain}
\]

Those are not the same contribution. MioHint is evidence that LLM source comprehension can solve hard request-side predicates, but notably its evaluation explicitly excludes systems/targets whose relevant conditions are database operations or configurations because its request mutation mechanism cannot change them. That exclusion is unusually strong evidence for your gap: **the newest source-aware LLM REST tester identifies exactly the boundary you want to cross.** citeturn15view0turn14view3

Consider a sink guarded by something like:

```java
User existing = repo.findByEmail(req.email());

if (existing != null
        && existing.getTenantId().equals(req.tenantId())
        && !existing.isDisabled()) {
    log.warn(...);   // target sink
}
```

A SQL-feedback searcher can eventually learn from an executed query that a matching row is needed and evolve DB records toward it. A source-semantic approach could instead infer in one reasoning step that it needs, schematically,

```sql
INSERT INTO users(email, tenant_id, disabled, ...)
VALUES(request.email, request.tenantId, false, ...);
```

subject to schema/foreign-key constraints. **That source-to-relational-precondition translation is the promising novelty**, not the subsequent execution of the `INSERT`, because the latter is already explicitly supported by EvoMaster. citeturn18view0turn18view1

There are several nontrivial research problems here that would make such a contribution stronger than “ask GPT to write SQL”: repository/ORM resolution (`JPA`, Spring Data derived queries, MyBatis, query builders); relationships and foreign keys; distinguishing “must exist” from “must not exist” for uniqueness checks; deriving multiple mutually consistent rows across tables; handling transactions and caches; and validating that an LLM-generated setup corresponds to the **target branch** rather than merely satisfying the SQL syntax. The NoSQL work reinforces that database modeling itself changes substantially with the persistence technology. citeturn18view1

A particularly defensible architecture would therefore use the LLM for **semantic hypothesis generation**, not as the database oracle: statically extract the sink slice, ORM/entity/schema metadata and relevant query; ask the LLM to propose a symbolic seed predicate or tuple; deterministically check schema/foreign-key/type constraints; execute the seed in an isolated test DB; then use coverage/query feedback to verify the predicted prerequisite. This mirrors APIPilot's important lesson that LLM-inferred dependencies should be treated as hypotheses and verified concretely. citeturn21view1

## Exception-path prerequisites and crash reproduction

RQ3 is materially less mature at the HTTP/service level than RQ2.

Classic **crash reproduction** starts with an observed failure, usually represented by a Java stack trace, and searches for a test capable of producing the same crash. EvoCrash's original Guided Genetic Algorithm work appeared at ICSE 2017, DOI `10.1109/ICSE.2017.27`; the extended study *Search-Based Crash Reproduction and Its Impact on Debugging* has DOI `10.1109/TSE.2018.2877664` and appeared in IEEE TSE volume 46 in 2020. The important idea is to make reproduction of the specified crash/stack-trace location the search objective instead of generic coverage. citeturn20search11

The subsequent JCrashPack study, *A Benchmark-Based Evaluation of Search-Based Crash Reproduction*, DOI `10.1007/s10664-019-09762-1`, assembled a much larger Java crash-reproduction benchmark and evaluated EvoCrash systematically. This literature demonstrates that **exception/crash identity can be a directed testing objective**, but the generated artifacts are principally unit-level Java tests and object/method-call sequences, not external HTTP workflows through a running microservice. citeturn19search19turn20search11

This distinction is important for your catch-block sinks:

```java
try {
    Foo x = service.parse(request.getPayload());
    ...
} catch (IllegalArgumentException e) {
    log.warn(request.getPayload());   // target sink
}
```

The prerequisite is not naturally:

> endpoint A produces an ID used by endpoint B.

It is:

> input \(x\) must drive some throw site \(t\) so that the dynamic exception object is compatible with catch type \(E\), while execution reaches \(t\) through the HTTP/framework/service layers and does not get rejected earlier.

RESTler/MOREST-style dependency graphs have no first-class representation for that relation. DeepREST may discover such inputs accidentally through interaction, and EvoMaster can seek internal branch coverage and detect server errors, but neither the classical producer–consumer model nor HTTP status-code feedback directly explains **which internal exception must be synthesized**. citeturn13search0turn19search1turn18view0

MioHint is the most important recent bridge. It identifies hard-to-cover source targets, performs **cross-file and cross-procedure statement-level def-use analysis** to retrieve code relevant to the target, sends that reduced context plus the current test input to GPT-4o, and asks for a precise mutation hint. In its motivating example, the LLM understands parsing/regex semantics and directly produces a string satisfying a previously difficult target predicate. This is exactly the type of source-to-HTTP reasoning your exception module would need. ICSE 2026 / arXiv:2504.05738. citeturn15view1turn14view3

Yet MioHint does **not** present exception semantics as a dependency model. Its paper describes target conditions/fitness plateaus and request mutation; searching the current paper text reveals no dedicated exception-generation mechanism. It also reports that database/configuration-related targets were out of scope for its request mutation strategy. citeturn15view0turn15view2

That leaves a clear gap. In the REST/microservice papers reviewed here, I found no system whose core abstraction is:

\[
\text{HTTP input}
\rightarrow
\text{throw site}
\rightarrow
\text{specific exception type}
\rightarrow
\text{specific catch}
\rightarrow
\text{target sink}.
\]

A technically strong approach would model exception prerequisites explicitly. Starting from the target catch/sink, static analysis can enumerate throw-capable statements on protected paths and exception types assignable to the catch type; backward slicing can connect throw-site operands to HTTP-originated data; the LLM can then reason about library/application semantics that make the operation throw; runtime instrumentation can verify `(throw-site, dynamic exception class, catch handler, sink)` rather than treating a generic 500 response as success. MioHint motivates the source-comprehension part, while EvoCrash motivates an **exception-specific directed objective**. citeturn14view3turn20search11

This would also distinguish two fundamentally different exception prerequisites:

**Input-induced exceptions** are reachable by malformed or semantically adversarial request data—for example parser, conversion, domain-validation, indexing, or unsupported-value behavior. These are a natural fit for LLM-guided HTTP mutation similar to MioHint. citeturn15view1

**Environment-induced exceptions** require a failing database, remote dependency, timeout, corrupted stored record, race, or similar condition. HTTP mutation alone cannot necessarily synthesize them. EvoMaster explicitly notes that error-related scenarios involving external services are difficult without control over those services and therefore supports search-generated service mocks. This suggests that an exception-precondition solver should allow setup actions other than request mutation—DB seed, mock response, fault injection, or service-state manipulation. citeturn18view0

For your `exception_path` bucket, this distinction is worth measuring empirically. It may turn out that a meaningful fraction of the 18% can be solved purely by malformed request construction, while another fraction fundamentally needs **environment-state synthesis**, which would make a unified prerequisite planner substantially more valuable.

## What LLM-based REST testing actually uses the LLM for

RQ4 has a surprisingly crisp answer: **most successful LLM REST testers do not let the LLM do everything**.

RESTGPT is predominantly a **specification semantic parser**. It reads natural-language parameter descriptions, extracts machine-readable constraints such as inter-parameter dependencies, and proposes example values; it then augments the OpenAPI specification so conventional testing tools can exploit that information. It does not read service source code, derive DB rows, or principally solve operation chains itself. citeturn19search2

KAT gives the LLM a broader role. It constructs an operation dependency graph from OpenAPI and uses GPT-centered prompting in generation of test scripts, constraint-validation scripts, test cases and test data. Its motivating example explicitly recognizes a hidden prerequisite: booking needs an existing `flightId`, so `GET /flights` must run first and a returned ID must be propagated to the booking request. This is genuine sequence/precondition reasoning, but the knowledge comes from the API specification/semantics rather than implementation source or DB queries. citeturn14view1turn21view0

AutoRestTest is often described as “LLM + agents,” but the division of labor is important. Its **Semantic Property Dependency Graph is constructed using OpenAPI structure and GloVe semantic similarity**, while multiple Q-learning agents choose operations, dependencies, parameter combinations, values and headers. The LLM is specifically used to supply realistic inputs for value/header generation. Thus its SOTA lesson is not “LLM plans the entire call chain”; graph structure and reinforcement learning remain the sequence/search engine. citeturn21view2

LlamaRestTest is even more specialized. One fine-tuned Llama3-8B derivative, LlamaREST-IPD, predicts inter-parameter dependency rules; a second, LlamaREST-EX, produces realistic parameter values. The novel runtime feature is to incorporate **server response messages** after repeated failures so the next prediction can correct the parameter combination/value. Removing the IPD module reduced branch coverage by 21.9% in its ablation; removing server-response information reduced branch coverage by 15.5%. On the evaluated open-source services, the strongest 8-bit configuration obtained 55.8% method, 28.3% branch and 55.3% line coverage and reported 204 internal-server-error instances under the paper's counting procedure. DOI `10.1145/3715737`, arXiv:2501.08598. citeturn14view2

MioHint finally moves the LLM **inside the white-box loop**. The LLM sees selected implementation code, the uncovered target and an existing test; its job is not to freely invent a JUnit/system test but to output a targeted mutation hint. Static analysis reduces the repository to the cross-file value-flow context relevant to the target. Across 16 Java REST services, it reports a 4.95 percentage-point absolute mean line-coverage improvement over EvoMaster, a 67× improvement in mutation accuracy, and coverage of over 57% of the selected hard targets compared with below 10% for the baseline. citeturn14view3

APIPilot, the newest work in this survey, arguably gives the best architectural lesson for your project: use the LLM to make **semantic proposals that deterministic analysis cannot easily make**, but do not trust those proposals as facts. APIPilot lets the LLM propose producer–consumer relationships, concrete executions validate those relationships, a conventional graph algorithm constructs workflows, and runtime responses refine the state. citeturn21view1

The evolution can therefore be summarized as:

| LLM system | LLM generates sequence? | LLM generates parameters? | LLM reads server source? | LLM generates setup state/code? |
|---|---:|---:|---:|---:|
| RESTGPT | No, not its primary role | **Yes** | No | No; augments OAS only. citeturn19search2 |
| KAT | **Participates substantially** via dependency/test-script generation | **Yes** | No | API-call setup scripts, but not direct DB seed inferred from source. citeturn14view1 |
| AutoRestTest | No—the graph/MARL agents drive ordering | **Yes** | No | No direct DB setup. citeturn21view2 |
| LlamaRestTest | No; built around ARAT-RL testing | **Yes, strongly** | No | No; server feedback refines parameter rules/values. citeturn14view2 |
| MioHint | Mutates an existing target-reaching candidate rather than planning arbitrary workflow | **Yes, target-directed** | **Yes** | **No DB setup**; this is an explicitly exposed limitation. citeturn15view0turn15view1 |
| APIPilot | **No as final authority**; deterministic traversal constructs workflows | Some semantic constraint refinement | No | HTTP/resource state only | LLM dependency hypotheses are execution-verified. citeturn21view1 |

CodaMosa, ICSE 2023, DOI `10.1109/ICSE48619.2023.00085`, is useful as an adjacent rather than REST-specific result. It runs conventional search-based test generation until progress stalls, then asks Codex to produce examples that may break the coverage plateau. Its conceptual influence on MioHint is obvious, but CodaMosa works at Python unit-test/function scope and does not solve HTTP protocol state, persistent DB setup, or service-level exception prerequisites. citeturn0search2turn0search14

Similarly, Pythia, arXiv:2005.11498, is an important non-LLM adjacent system: it builds on stateful REST grammars and learns statistical patterns from valid requests to guide mutations while preserving enough structure to reach deep behavior, reporting 29 new bugs in its production-scale evaluation. It improves *input mutation after stateful sequencing* rather than solving DB or exception setup as explicit prerequisites. citeturn6search3

The publication-status distinction is important when using these as baselines. RESTler, MOREST, DeepREST, KAT, RESTGPT, AutoRestTest, LlamaRestTest and MioHint all have verified peer-reviewed venues; RESTGPT is specifically an **NIER** paper rather than a full ICSE research-track article. APIPilot is especially relevant and extremely recent, but as of August 20–September 6, 2026 the version I could verify is **arXiv:2608.17546v2**, so it should be labeled a preprint rather than silently promoted to conference SOTA. citeturn21view1turn19search2turn13search19

## Novelty and collision risk for the proposed approach

Your proposed system can be stated more precisely as:

> Given a designated Java log sink, perform target-directed source analysis to identify non-HTTP prerequisites; use an LLM over a minimized source/data-flow slice to translate a DB-dependent branch into concrete seed-state constraints or an exception-dependent path into a concrete exception-trigger strategy; instantiate those prerequisites through direct DB seeding or HTTP input construction; then validate sink reachability dynamically.

Under that formulation, I would assess the prior-art risk as follows.

### Database seeding alone has high collision risk

A claim such as:

> “We improve REST API reachability by automatically generating records and inserting them directly into MySQL before invoking HTTP endpoints.”

is **not novel**. EvoMaster's SQL work already monitors SQL interactions, reasons about unsatisfied query predicates, synthesizes initialization data, and directly inserts database records when HTTP setup is unavailable or too complicated. It even treats database initialization as part of the generated test. citeturn18view0turn18view1

The novelty has to be in something stronger, for example:

> “Starting from a designated sink and without first relying on repeated execution of its SQL query, we derive the persistent-state predicate from source/ORM/data-flow context, use an LLM to instantiate a minimal relational witness, and verify it against the schema and sink execution.”

That is substantially different from EvoMaster's SQL-distance/evolution loop. The strongest experimental comparison would therefore be **time-to-target / target-hit rate / number of executions needed** against EvoMaster SQL handling, not merely aggregate branch coverage.

An especially convincing benchmark should contain predicates of the forms you actually observe:

```text
exists(row)
not exists(row)              // uniqueness / duplicate path
row.a == request.x
row.a != request.x
row.a == constant
row.fk -> second_table(...)
count(query) > k
optional/query empty vs non-empty
stored enum/state == X
```

because generic “DB causes coverage changes” would overlap too strongly with existing work. EvoMaster's existing SQL techniques are specifically designed for query-driven coverage, so the research question should be whether **semantic target-directed synthesis reaches these states faster or reaches states search-based SQL feedback fails to construct**. citeturn18view1

### Source-aware LLM request generation now has medium-to-high collision risk

Before MioHint, “LLM reads implementation source and generates an HTTP value satisfying a hidden condition” would have sounded quite novel. In September 2026 it no longer is.

MioHint's core example is precisely:

\[
\text{hard branch target}
+
\text{cross-procedure source slice}
+
\text{current HTTP test}
\stackrel{\text{GPT-4o}}{\longrightarrow}
\text{specific input mutation}.
\]

It is an ICSE 2026 paper and reports substantial improvements over EvoMaster. citeturn15view1turn13search19

So a paper whose exception contribution is merely:

> “Feed source surrounding an uncovered catch block to GPT-4o and ask it for malformed HTTP parameters.”

would face a serious “this is MioHint specialized to a catch target” reviewer objection.

The differentiator should instead be that **exception semantics are modeled explicitly**. For example, rather than target only the catch block:

\[
T = \text{catch block covered},
\]

define a prerequisite relation such as

\[
P =
(\text{request field } r)
\leadsto
(\text{throw site } t)
\leadsto
(\text{exception class } E)
\leadsto
(\text{catch handler } h)
\leadsto
(\text{sink } s).
\]

Then use static exception analysis to enumerate candidate `t`/`E`, interprocedural value-flow to identify request-controlled operands, LLM reasoning to synthesize semantics-heavy triggers, and instrumentation to verify the exact dynamic exception/catch pair. MioHint supplies the LLM/data-flow ingredients, but its published abstraction is target-specific mutation, not an exception-precondition graph. citeturn14view3turn15view2

### The strongest novelty is a unified prerequisite-state abstraction

The more defensible contribution is not “LLM + SQL” or “LLM + fuzzing.” It is a **white-box prerequisite planner for designated Web-reachable sinks** in which different hidden state types become explicit setup actions:

\[
\begin{array}{ll}
\text{resource dependency} &
\rightarrow \text{preceding API call} \\
\text{authentication dependency} &
\rightarrow \text{credential/session bootstrap} \\
\text{DB existence predicate} &
\rightarrow \text{API create or direct DB insert} \\
\text{DB nonexistence predicate} &
\rightarrow \text{delete/reset/isolated DB} \\
\text{DB field comparison} &
\rightarrow \text{relational tuple synthesis} \\
\text{input-induced exception} &
\rightarrow \text{targeted malformed HTTP value} \\
\text{remote-service exception} &
\rightarrow \text{mock/fault setup} \\
\end{array}
\]

The SOTA currently has these ingredients in **separate research traditions**: RESTler/APIPilot for HTTP producer–consumer chains, EvoMaster for direct DB state, MioHint for source-aware target mutations, EvoCrash for exception-specific reproduction, and EvoMaster's mocking support for external-service error scenarios. citeturn21view1turn18view0turn14view3turn20search11

Your novelty becomes much stronger if the planner is generated **backward from the sink**, rather than trying to maximize global code coverage. A log-injection reachability experiment has a natural objective:

\[
\text{success}(S,R)=
\begin{cases}
1 & \text{if HTTP workflow }R\text{ dynamically executes sink }S\\
0 & \text{otherwise.}
\end{cases}
\]

This is qualitatively different from aggregate branch/line coverage and from “discover as many HTTP 500s as possible.” MioHint is target-oriented, so target orientation alone is not sufficient; the distinctive point is the **heterogeneous prerequisite synthesis** behind the target. citeturn14view3

### The three closest works

**Closest for `db_state`: Arcuri & Galeotti, *Handling SQL Databases in Automated System Test Generation*, TOSEM 2020, DOI `10.1145/3391533`.** This is the highest collision risk. It already observes SQL predicates, defines database-aware fitness, evolves seed records and performs direct DB insertion. Your difference must be source/ORM-level semantic inference, designated-sink backward reasoning, LLM synthesis, and ideally dramatically fewer executions than evolutionary DB search. The SSBSE 2021 integration, DOI `10.1007/978-3-030-88106-1_8`, should be treated as part of the same prior-art family. citeturn16search1turn16search0turn18view0

**Closest for source-to-HTTP reasoning: MioHint, *LLM-Assisted Request Mutation for Whitebox REST API Testing*, ICSE 2026, arXiv:2504.05738.** It already performs interprocedural source/data-flow reduction and asks GPT-4o to construct target-specific request mutations. The critical difference is that MioHint does not synthesize database initialization and explicitly excludes DB/configuration targets its request mutation cannot handle; nor does it make exception type/throw-site/catch relationships a first-class precondition. citeturn14view3turn15view0

**Closest for `exception_path`: EvoCrash, *Search-Based Crash Reproduction and Its Impact on Debugging*, IEEE TSE, DOI `10.1109/TSE.2018.2877664`, following the ICSE 2017 guided-genetic-algorithm paper DOI `10.1109/ICSE.2017.27`.** EvoCrash establishes the principle of turning a specific Java failure/stack trace into a directed test-generation objective. Your difference is the abstraction boundary: externally initiated HTTP/microservice execution rather than direct Java unit calls, target `catch` reachability rather than reproduction of an already observed terminating crash, and explicit orchestration of DB/API/environment setup preceding the exception. citeturn20search11

APIPilot is the closest **multi-step orchestration** work and should probably be an additional comparison even though it is not one of those three component-level collisions. Its August 2026 result raises the bar for LLM dependency papers: a reviewer can reasonably ask why an LLM-inferred dependency should be trusted. Your system should answer the same way APIPilot does—**treat LLM output as a candidate setup hypothesis and dynamically validate it before declaring the sink reachable.** citeturn21view1

## Bottom line for the proposed research contribution

The literature supports a fairly strong conclusion:

**RQ1 — multi-step API prerequisites are well studied.** Producer–consumer graphs, resource-ID propagation, model correction, reinforcement-learning-based hidden ordering, semantic graphs and, most recently, execution-validated LLM dependency hypotheses all exist. A new paper cannot claim novelty merely from generating multi-request HTTP chains. RESTler → MOREST → DeepREST/KAT/AutoRestTest → APIPilot provides a clear prior-art trajectory. citeturn13search0turn19search1turn21view1

**RQ2 — database prerequisites are partially solved, and this is the biggest collision.** EvoMaster already gives automated Web API testing control over relational DB state, including SQL feedback and direct insertion; the idea has now been extended to MongoDB. What appears genuinely open is **semantic, target-directed derivation of a minimal DB witness from implementation source/ORM logic**, particularly using an LLM, rather than evolving DB genes from SQL execution feedback. citeturn18view0turn18view1

**RQ3 — exception prerequisites remain much more open at the HTTP boundary.** Crash-reproduction research knows how to direct search toward a specified Java failure, and MioHint knows how to use source-aware LLM reasoning to generate REST input for a hard branch, but I found no verified REST/microservice SOTA that explicitly solves `HTTP input → specific throw site/type → desired catch → designated sink`. That is the cleanest research gap in the proposed project. citeturn20search11turn14view3

**RQ4 — the strongest LLM designs are hybrid rather than end-to-end generative.** RESTGPT uses the LLM for semantic rule/value extraction; LlamaRestTest for parameter dependencies and values; AutoRestTest combines an LLM with a semantic graph and RL agents; MioHint uses static analysis to restrict the source context before asking the LLM for a request mutation; APIPilot uses the LLM only to hypothesize semantic dependencies and then validates them by execution. citeturn19search2turn14view2turn21view2turn14view3turn21view1

Accordingly, the most defensible paper claim is **not**:

> “We use an LLM to generate setup steps for REST testing.”

and not:

> “We seed MySQL to reach deeper branches.”

Both are too close to existing work. citeturn18view0turn14view3

A much stronger claim would be:

> **We introduce sink-directed prerequisite synthesis for Web API reachability. Starting from a designated server-side sink, the technique performs interprocedural analysis to classify and reconstruct hidden prerequisites, including persistent database predicates and exception-producing conditions. An LLM reasons over a statically minimized implementation slice to propose concrete DB witnesses or HTTP exception triggers; deterministic schema/type analysis and runtime instrumentation validate each proposal. Unlike stateful API fuzzers, it can synthesize state not producible from API responses; unlike SQL-aware EvoMaster, it reasons backward from a target instead of evolving DB state from SQL fitness; and unlike MioHint, it can change persistent/environment state rather than only mutating HTTP request inputs.**

That formulation has **moderate overall collision risk but a credible novelty window** as of September 6, 2026. The DB half must be positioned very carefully against EvoMaster, and the source-aware input half very carefully against MioHint. The **most novel part appears to be explicit exception-precondition synthesis at the HTTP/microservice boundary and the unification of DB, exception, resource and authentication prerequisites into one sink-directed setup planner**. citeturn18view0turn15view0turn20search11turn21view1