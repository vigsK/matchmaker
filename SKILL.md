---
name: aws-query-reconciliation-architect
description: >
  Design and implement an enterprise-grade, serverless AWS architecture for automated,
  scheduled cross-database query-result reconciliation (Oracle ↔ Snowflake) using a custom
  containerized Python image, orchestrated by AWS Step Functions (Distributed Map) over
  ECS Fargate / AWS Batch, with ECR for images and EventBridge Scheduler for daily runs.
  Use this skill when: moving a "run-the-same-query-on-two-databases-and-compare" workload
  to AWS; building containerized scheduled batch jobs orchestrated by Step Functions;
  parallelizing long-running DB jobs while protecting source databases; or any design that
  must minimize operational overhead and bill on-demand (pay-per-use) for spiky workloads.
---

# AWS Cross-Database Query Reconciliation — Architecture Skill

> Reference architecture + decision playbook for the workload described in `QUERY_MATCHING.md`:
> an Excel file of paired SQL queries (one for **Oracle**, one for **Snowflake**) is read by a
> Python script that runs each pair against both databases and verifies the results are
> **consistent**. The whole thing must move to AWS, run **daily but spiky**, use **custom
> container images**, be orchestrated with **Step Functions**, be **fast**, and have **minimal
> operational overhead** on **on-demand pricing**.

---

## 0. How to use this skill

1. Read **§1 Requirements** and confirm the **§2 Assumptions** with the user — three of them
   (where Oracle lives, whether Harbor is mandatory, and how many query-pairs) materially change
   the design. The doc already branches on all three, so you can proceed without blocking.
2. Default to the **Recommended Architecture (§4, Variant A)**. Drop to **Variant C (monolith)**
   if there are only a handful of query-pairs; escalate to **Variant B (Batch)** or **Variant D
   (EKS)** only when the triggers in §8 fire.
3. Build in the order given by the **§9 Implementation playbook**.
4. Every service choice has a *why* and a *tradeoff* in **§5**. Cite those when challenged.

---

## 1. Requirements (distilled from the brief)

**Functional**
- F1 — Read an Excel/CSV of N rows; each row = `{oracle_sql, snowflake_sql}` (a "query-pair").
- F2 — Execute each query on its database; compare the two result sets for **consistency**
  (row counts, values, ordering-independent equality).
- F3 — Produce a per-pair verdict (MATCH / MISMATCH / ERROR) and an aggregate reconciliation report.
- F4 — Run **automatically every day**; also support **ad-hoc / on-demand** runs.

**Non-functional**
- NF1 — **Minimal operational overhead** → prefer fully-managed / serverless; no servers to patch.
- NF2 — **On-demand / pay-per-use** pricing → jobs run *inconsistently*; idle cost ≈ $0.
- NF3 — **Low latency / high throughput** → queries are long-running; parallelize aggressively
  but **never overload the source databases** (throttle concurrency).
- NF4 — **Enterprise-grade** → secured, encrypted, audited, least-privilege, observable, repeatable (IaC).
- NF5 — "Open to **monolithic**" → a single cohesive codebase/image is fine; we do **not** need
  microservices. (We deliver a *modular monolith app* on a *distributed execution fabric*.)

**Hard constraints stated in the brief**
- C1 — Custom **Python base image** with deps, built in a **build phase**, pushed to **Harbor or
  similar** (a private registry).
- C2 — **Step Functions** must orchestrate (because query runtime is high).
- C3 — Containers run the **pulled custom image** on "clusters."

---

## 2. Assumptions to confirm (each one branches the design)

| # | Assumption (default taken) | If false → change |
|---|---|---|
| A1 | **Oracle is on-premises** (the classic Snowflake-migration reconciliation pattern). | If Oracle is **RDS/RDS Custom for Oracle** or **OCI** → it's in-VPC or reachable directly; drop the Direct Connect/VPN section (§5.7a). |
| A2 | **Harbor is illustrative, not mandatory.** We recommend **Amazon ECR** for lowest ops overhead. | If Harbor is a **mandated enterprise standard** → keep Harbor, self-host it on EKS/EC2, pull from it (§5.1, "Harbor path"). |
| A3 | **N is large enough to parallelize** (dozens–thousands of pairs, long runtimes). | If N ≤ ~10 and runtimes are short → **Variant C monolith** is simpler and cheaper. |
| A4 | "~50 characters" in the brief is a typo; queries are **long-running/expensive SQL**. Comparison loads **both result sets into pandas DataFrames** in the task. | If results are *huge* (multi-GB/pair), **chunk** the DataFrame compare by key range and/or add a checksum pre-filter (§5.6) so you don't OOM the task or pull identical sets needlessly. |
| A5 | Daily run + occasional re-runs; **no sub-minute latency SLA**. | If near-real-time → this batch design is wrong; use streaming/CDC instead. |

---

## 3. Reference architecture (Variant A — recommended)

**One-liner:** *EventBridge Scheduler* kicks a *Step Functions Standard* state machine that uses a
*Distributed Map* to fan out one *ECS Fargate task* per batch of query-pairs (custom image from
*ECR*), each task reconciles Oracle↔Snowflake over private links, writes verdicts to *S3*, and a
final step aggregates + notifies via *SNS*. Everything is serverless and billed only while running.

```
                                  ┌──────────────────────────────────────────────────────────────┐
   DEV / CI  (build once)         │                        AWS ACCOUNT                             │
 ┌───────────────────┐           │                                                                │
 │ Git (CodeCommit/  │  push     │   ┌───────────────┐   build &    ┌──────────────────────────┐  │
 │ GitHub) Dockerfile├──────────▶│   │  CodePipeline │────scan─────▶│ Amazon ECR (private)      │  │
 │ + requirements.txt│           │   │  + CodeBuild  │  (Inspector) │  python-reconciler:<sha>  │  │
 └───────────────────┘           │   └───────────────┘              └─────────────┬────────────┘  │
                                 │                                                │ pull (PrivateLink)
                                 │   ┌──────────────────┐  start exec             │                │
   ⏰ daily cron / ad-hoc ──────▶│   │ EventBridge       │──────────┐             │                │
                                 │   │ Scheduler (tz,    │          ▼             │                │
                                 │   │ retries, flex win)│   ┌────────────────────┴───────────────┐│
                                 │   └──────────────────┘   │  Step Functions  (STANDARD)         ││
                                 │                          │  ┌───────────────────────────────┐  ││
   S3: manifest (CSV/JSON of     │   Excel→CSV/JSON         │  │ 1. Prep: Excel→manifest in S3 │  ││
   query-pairs)  ◀───────────────┼───────────────────────  │  │ 2. DISTRIBUTED MAP            │  ││
        │  ItemReader            │                          │  │    ├ ItemReader: S3 manifest │  ││
        └────────────────────────┼─────────────────────────┼─▶│    ├ ItemBatcher: K pairs/job│  ││
                                 │                          │  │    ├ MaxConcurrency: throttle│  ││
                                 │   ┌──────────────────────┼──┤    └ child: ecs:runTask.sync │  ││
                                 │   ▼ (private subnets)    │  │ 3. Aggregate (Athena/Lambda) │  ││
                                 │  ┌─────────────────────┐ │  │ 4. Notify (SNS)              │  ││
                                 │  │ ECS Fargate task    │ │  └───────────────────────────────┘  ││
                                 │  │ (custom image)      │ │           ResultWriter → S3         ││
                                 │  │  reconciler.py      │ └─────────────────────────────────────┘│
                                 │  │  • read pair        │                                         │
                                 │  │  • run on Oracle    │   ┌──────────────┐  Secrets Manager     │
                                 │  │  • run on Snowflake │──▶│ VPC endpoints│  (DB creds, rotated)  │
                                 │  │  • compare (pandas) │   │ ECR/S3/Logs/ │  KMS (encrypt all)    │
                                 │  │  • write verdict→S3 │   │ Secrets/SFN  │                       │
                                 │  └────────┬───────┬────┘   └──────────────┘                       │
                                 └───────────┼───────┼─────────────────────────────────────────────┘
                            PrivateLink ┌────▼───┐ ┌─▼────────────┐  Direct Connect / S2S VPN
                            (interface) │Snowflake│ │ Transit GW   ├───────────────▶  ON-PREM
                                        │ (SaaS)  │ │ + Route53 RSLV│   port 1521     ORACLE DB
                                        └─────────┘ └──────────────┘
```

**Results / reporting plane (right side, expanded):**

```
 S3 (verdicts, partitioned by run-date)
   ├─ SUCCEEDED/…   ├─ FAILED/…   (ResultWriter separates by status)
        │
        ├──▶ Glue Catalog + Athena  (query "show me today's mismatches")
        ├──▶ QuickSight dashboard   (trend of match-rate over time) [optional]
        └──▶ SNS topic ──▶ email / Slack / PagerDuty  (alert on MISMATCH or run failure)
```

---

## 4. End-to-end data flow

1. **Build (once per code change).** Git push → CodePipeline → CodeBuild builds the custom Python
   image (multi-stage, deps baked in), tags it with the **git commit SHA**, pushes to **ECR**;
   ECR **enhanced scanning** (Amazon Inspector) gates on CVEs. This satisfies constraint **C1**.
2. **Trigger.** **EventBridge Scheduler** fires daily (cron, with timezone) → `StartExecution` on
   the Step Functions **Standard** state machine. Same entry point is used for ad-hoc runs.
3. **Prep.** First state converts the Excel to a **manifest** (CSV or JSON-Lines) in **S3** — one
   record per query-pair. (Do the Excel→CSV conversion in a tiny Lambda or as the first container
   step; Distributed Map reads CSV/JSON natively, not `.xlsx`.)
4. **Fan-out.** The **Distributed Map** state's `ItemReader` streams the manifest from S3,
   `ItemBatcher` groups **K** pairs per child, and each child runs `ecs:runTask.sync` to launch a
   **Fargate task** from the ECR image. `MaxConcurrency` caps how many tasks hit the databases at
   once (the throttle that protects Oracle/Snowflake — **NF3**).
5. **Reconcile (inside each task).** `reconciler.py` pulls DB creds from **Secrets Manager**,
   connects to **Oracle** (over Direct Connect/VPN) and **Snowflake** (over PrivateLink), runs the
   pair, loads **both result sets into pandas DataFrames** and compares them (§5.6), then writes a verdict
   object to **S3**.
6. **Collect.** Distributed Map's `ResultWriter` consolidates child results to S3, separating
   `SUCCEEDED` from `FAILED` (no 256 KB payload problem — see §5.4).
7. **Aggregate + report.** A final state runs an **Athena** query (or Lambda) over the S3 verdicts
   to compute the run summary (match rate, list of mismatches) and writes the report to S3.
8. **Notify.** **SNS** publishes "run complete / X mismatches" to email/Slack; failures raise a
   CloudWatch alarm and an EventBridge failure event.

---

## 5. Component decisions (the "why" and the tradeoffs)

### 5.1 Container registry — **Amazon ECR** (recommended) vs **Harbor**

The brief says "Harbor *or any similar service*." On AWS, the lowest-operational-overhead choice is
**Amazon ECR**, which is the native registry and removes an entire self-managed component.

| Capability | **Amazon ECR (recommended)** | **Harbor (self-managed)** |
|---|---|---|
| Ops overhead | **Zero** — fully managed | You run it (on EKS/EC2): HA, DB, storage, upgrades, backups |
| AuthN/Z | **IAM** (no extra creds in tasks) | Harbor users/robots → creds live in Secrets Manager |
| Private pull | **PrivateLink** interface endpoint (no NAT) | Needs network path + creds |
| Scanning | Basic + **Enhanced (Inspector)** CVE scanning | Trivy/Clair built-in, SBOM on push |
| Encryption | **KMS** at rest, TLS in transit | You configure |
| Lifecycle/GC | **Lifecycle policies** (expire old tags) | Tag retention/GC jobs |
| Multi-cloud | AWS-only | **Multi-cloud / multi-registry replication** ← Harbor's main edge |
| Pull-through cache | Mirrors Docker Hub, Quay, GHCR, GitLab, ECR Public, Azure, Chainguard, k8s. **Cannot** use Harbor as an upstream. | Can proxy/cache upstreams incl. ECR |

**Decision:** Use **ECR**. It satisfies **C1** ("pushed into Harbor *or similar*") while honoring
**NF1** (minimal ops) and **NF4** (IAM + KMS + scanning + PrivateLink out of the box). Use an **ECR
pull-through cache rule** for the upstream `python:3.x-slim` base image so the build never hits
Docker Hub rate limits.

**Harbor path (only if mandated, A2=false):** Keep Harbor as the org-wide registry (e.g., for
multi-cloud governance, advanced RBAC projects, SBOM-on-push). Host it on EKS/EC2 in a shared
services account. ECS/Batch pull from Harbor using a **`repositoryCredentials`** secret in Secrets
Manager. You accept the extra operational surface (Harbor itself is now something you patch and keep
HA) in exchange for registry portability. *This is the one place the brief's wording pulls against
NF1 — flag the tradeoff explicitly.*

### 5.2 Build / CI pipeline — **CodePipeline + CodeBuild → ECR**

- **CodeBuild** project in **privileged mode** (Docker-in-Docker) runs a `buildspec.yml`:
  `pre_build` logs into ECR, `build` does a **multi-stage** `docker build` (small final image),
  `post_build` pushes. Tag with `CODEBUILD_RESOLVED_SOURCE_VERSION` (the commit SHA) for
  immutable, traceable images.
- Use **Buildx remote cache stored in ECR** so dependency layers are reused across builds → faster,
  cheaper builds (deps like `oracledb`, `snowflake-connector-python`, `pandas` are heavy).
- Alternatively, the newer **CodePipeline `ECRBuildAndPublish` action** builds+pushes **without a
  buildspec** and can run a vulnerability scan inline — fewer moving parts.
- Gate promotion on the **ECR/Inspector scan** result (fail the pipeline on Critical CVEs).

*Why CodeBuild and not "just GitHub Actions"?* Either works; CodeBuild keeps the build inside the
account boundary (IAM, VPC, KMS) with no extra OIDC federation to manage — better for **NF4** and
**NF1**. If the org already standardizes on GitHub Actions, federate via OIDC and push to ECR.

### 5.3 Compute engine — **ECS Fargate** (recommended) vs Batch vs EKS vs Lambda

This is the most consequential choice. The container runs long, the workload is **spiky**, and ops
overhead must be minimal.

| Engine | Fit for this workload | Ops overhead | On-demand $ | Verdict |
|---|---|---|---|---|
| **ECS Fargate task** (via SFN `runTask.sync`) | **Excellent.** Serverless, fast scale-out, no cluster, per-task isolation, awsvpc networking, supports **Fargate Spot**. | **Lowest** | Pay per task-second | ✅ **Recommended** |
| **AWS Batch on Fargate** | **Excellent for "job" semantics**: managed queue, array jobs, job dependencies, automatic retries, fair-share priority. Still serverless (Fargate). | Low (compute env + queue + job def) | Pay per task-second | ✅ **Variant B** — pick when you think in jobs/queues, need priorities/retries/array fan-out |
| **EKS / Batch-on-EKS** | Powerful but **heaviest**: cluster, node groups, add-ons, upgrades, autoscaler. | **Highest** | Nodes can idle | ⚠️ **Variant D** — only if K8s/Harbor is already the org standard |
| **AWS Lambda** | **Wrong.** 15-min cap can't hold "high runtime" queries; not built for long DB joins. | Lowest | Pay per ms | ❌ for the worker; ✅ for glue (Excel→CSV, aggregation) |

**Fargate sizing knobs** (per worker task): **0.25–16 vCPU**, **0.5–120 GB** memory, **20–200 GB**
ephemeral storage. **Because the DataFrame compare (§5.6) holds both full result sets in RAM, memory
is the binding dimension** — budget ~5–10× the largest raw result set and pick a memory-heavy
profile (e.g., **2 vCPU/16 GB** or **4 vCPU/30 GB**); the cheapest 0.25–0.5 vCPU sizes can't hold
large frames. Use `ItemBatcher` to amortize task startup, and process pairs **serially within a
task** so memory stays sized to one pair, not the whole batch.

**Decision:** **Step Functions Distributed Map → `ecs:runTask.sync` on Fargate.** It is serverless
(**NF1**), bills only while a task runs (**NF2**), scales out fast for low latency (**NF3**), and
runs your **custom image** unmodified (**C3**). Step Functions is doing the orchestration the brief
mandates (**C2**), so adding AWS Batch's separate scheduler would be redundant *unless* you need
Batch-specific features (then → Variant B).

### 5.4 Orchestration — **Step Functions Standard + Distributed Map**

The brief explicitly wants Step Functions "as query runtime is high." Two sub-decisions:

**(a) Standard, not Express.** Express workflows cap at **5 minutes** and are at-least-once —
unusable for long DB jobs and for the `.sync` job-run pattern. **Standard** runs up to **1 year**,
is **exactly-once**, supports `.sync` service integrations, and—critically—**Distributed Map's
DISTRIBUTED mode is only supported in Standard workflows** (not Express). The *child* workflows
inside the map can still be EXPRESS for cost if the per-child step is short; here each child waits
on a long Fargate task, so keep children **STANDARD** with `runTask.sync`.

**(b) Distributed Map, not inline Map.** Use **Distributed** mode when any of these is true (all
likely here): dataset **> 256 KiB**, execution history **> 25,000 events**, or you need
**> 40 concurrent** iterations. Distributed Map gives:

- **`ItemReader`** — streams the query-pair manifest **straight from S3** (CSV with
  `CSVHeaderLocation: FIRST_ROW`, or JSON-Lines). No 256 KB input ceiling.
- **`ItemBatcher`** — pack **K** pairs into each child so you don't pay per-task overhead for tiny
  units of work. Tune K so each Fargate task runs for minutes, not seconds.
- **`MaxConcurrency`** — the **database-protection throttle**. Default is **10,000** parallel
  children; set it to what Oracle/Snowflake can take (e.g., 20–50). *This is how you reconcile
  "prioritize speed" with "don't melt the source DBs."*
- **`ToleratedFailureCount` / `ToleratedFailurePercentage`** — let the run continue past a few bad
  pairs instead of failing everything (default 0 = any failure fails the run). Returns
  `States.ExceedToleratedFailureThreshold` when breached.
- **`ResultWriter`** — writes all child results to **S3**, split by `SUCCEEDED`/`FAILED`. Without
  it, Map returns an in-memory array that would blow the **256 KB** inter-state payload limit.
- **`TimeoutSeconds`** on the Map (e.g., **86400** = 24 h) so a stuck pair can't pin the run open.

Minimal ASL skeleton (JSONPath) for the map state — see §7 for the full snippet.

### 5.5 Scheduling — **EventBridge Scheduler** (not legacy Rules)

- **EventBridge Scheduler** (GA Nov 2022) is the modern choice: **time-zone-aware** cron, built-in
  **retries** (up to 185) + **flexible time windows** (jitter to avoid thundering herds against the
  DBs), **one-time** schedules that self-delete, millions of schedules, and a **universal target**
  that can call `states:StartExecution` directly. It's effectively free at this volume.
- Legacy **EventBridge Rules** still work and are free, but you hand-manage UTC/DST and are capped at
  ~300 rules/bus. Use Scheduler.
- **On-demand mapping (NF2):** Scheduler only *starts* the workflow; Fargate/Step Functions only
  bill while running, so the daily-but-spiky pattern incurs **no idle cost**. Ad-hoc runs are just a
  manual/API `StartExecution`.

### 5.6 The reconciliation logic — pandas DataFrame comparison

Each worker task loads **both** result sets into **pandas DataFrames** and compares them in memory:

1. **Fetch both sides into DataFrames.**
   - Oracle: `df_ora = pd.read_sql(oracle_sql, oracle_conn)` (via the `oracledb` driver).
   - Snowflake: `cur.execute(snowflake_sql); df_sf = cur.fetch_pandas_all()` — the Snowflake
     connector streams results as Arrow → pandas, far faster than row-by-row `fetchall()`.
2. **Normalize before comparing** (Oracle↔Snowflake type/collation differences are the **#1 source
   of false mismatches**): align column order and dtypes, **sort by the primary key**, reconcile
   `NULL`/`NaN`, trim strings/collation, round numerics to a common scale, and normalize
   timestamp/timezone formats. Bake these rules into `reconciler.py`.
3. **Compare with pandas.**
   - Verdict: `df_ora.equals(df_sf)` after sort/align, or
     `pandas.testing.assert_frame_equal(df_ora, df_sf, check_like=True, rtol=…, atol=…)` for
     tolerant numeric comparison.
   - Cell-level diff for the report: `df_ora.compare(df_sf)` → exactly which rows × columns differ.
   - Row-level set diff (rows present on only one side):
     `df_ora.merge(df_sf, how="outer", indicator=True)` then filter `_merge != "both"`.
4. **Write the verdict** (MATCH / MISMATCH / ERROR + a bounded diff sample) to S3, keyed by
   `run_id + pair_id` so Spot retries/re-runs are **idempotent** and never double-count (§5.11).

**Memory is the binding constraint of this design.** Both full result sets sit in RAM at once, and
pandas typically needs **~5–10× the raw result size** (object columns, the index, and the
`compare`/`merge` intermediates). Therefore:
- **Size the Fargate task's memory to the *largest* expected pair, not the average** (see the
  memory-heavy profiles in §5.3 and the pricing in §11). The cheapest 0.25–0.5 vCPU sizes can't hold
  large frames.
- For pairs whose results won't fit, **chunk** the compare by key range
  (`cur.fetch_pandas_batches()` on Snowflake, keyset pagination on Oracle) and diff chunk-by-chunk.
- **(Optional optimization)** Compute a cheap **row-count + aggregate checksum** on each side
  *first* (`STANDARD_HASH`/`ORA_HASH` on Oracle, `HASH`/`MD5` on Snowflake); pull the full
  DataFrames **only** when those disagree. This keeps the simple pandas path for real mismatches
  while avoiding multi-GB pulls when the sets are identical — directly cutting the **memory** and
  **data-transfer** costs modeled in §11.

> **Cost implication:** the DataFrame approach trades **higher task memory + more cross-link data
> transfer** (you pull both full sets every run, unless you add the checksum pre-filter) for
> implementation simplicity. This is why the Medium/Large profiles in §11 use 16–30 GB tasks and
> carry a real data-transfer line item.

### 5.7 Connectivity & secrets

**(a) Oracle (assume on-prem, A1).** Establish a private path from the VPC to the data center:
- **AWS Direct Connect** (preferred for enterprise: dedicated, low-jitter, predictable) **or**
  **Site-to-Site VPN** (IPsec, ≤ ~1.25 Gbps/tunnel) terminating on a **Transit Gateway**.
- Open **TCP 1521** from the Fargate subnets' CIDR through the corporate firewall.
- If you connect by hostname, add a **Route 53 Resolver outbound endpoint** to forward DNS to the
  on-prem resolvers.
- If Oracle is actually **RDS/OCI** (A1=false), it's reachable in-VPC or via its own PrivateLink —
  skip DX/VPN.

**(b) Snowflake (SaaS).** Use **AWS PrivateLink** so traffic never touches the public internet:
- In Snowflake, call **`SYSTEM$GET_PRIVATELINK_CONFIG`** to get the `privatelink-vpce-id`.
- Create an **interface VPC endpoint** to that service; allow **443/80** from the task security
  group; add **CNAME** records so the Snowflake account URL resolves to the endpoint.

**(c) Secrets.** All DB credentials in **AWS Secrets Manager** with **rotation**; prefer
**key-pair/OAuth** for Snowflake and a vaulted service account for Oracle over static passwords. The
task role reads secrets through a **Secrets Manager interface endpoint** (no internet).

**(d) Network posture.** Run Fargate tasks in **private subnets** across ≥2 AZs. Add **VPC
endpoints** for **ECR (api+dkr), S3 (gateway), CloudWatch Logs, Secrets Manager, Step Functions,
STS** so you can **drop NAT entirely** → cheaper *and* more secure (**NF1/NF4**, and removes NAT
data-processing cost).

### 5.8 Results, reporting, notification

- **S3** is the system of record for verdicts, partitioned `s3://…/recon/dt=YYYY-MM-DD/`. Encrypt
  with **KMS**; apply a **lifecycle policy** (e.g., Glacier after 90 days).
- **Glue Catalog + Athena** to query verdicts SQL-style ("today's mismatches", "match-rate trend").
  Serverless, pay-per-query — fits **NF1/NF2**.
- **QuickSight** (optional) for a match-rate dashboard for data-quality stakeholders.
- **SNS** topic for completion + mismatch alerts → email/Slack/PagerDuty. Wire **Step Functions
  Catch** + a **CloudWatch alarm** to the same topic for failures.

### 5.9 Observability & error handling

- **CloudWatch Logs** for every container (`awslogs` driver); structured JSON logs keyed by
  `run_id/pair_id`.
- **Step Functions execution history** + the **Map Run details** page give per-pair status, retries,
  and the failure threshold view — most of your "did it work?" questions are answered here.
- **Retries:** per-child `Retry` with backoff for transient DB/throttling errors; distinguish
  *retryable* (timeout, throttle) from *terminal* (bad SQL) so you don't retry forever.
- **Failure isolation:** `ToleratedFailureCount` keeps one bad query-pair from failing the batch;
  failed items land in the S3 `FAILED/` prefix for triage and targeted re-run.
- **X-Ray** (optional) for end-to-end traces; **EventBridge** failure events → on-call.

### 5.10 Security & governance (enterprise-grade, NF4)

- **IAM least privilege**: separate **task role** (read Secrets, write S3, talk to DBs) from
  **task execution role** (pull ECR, write Logs). The state-machine role gets only
  `states:StartExecution`/`DescribeExecution` for Distributed Map children + `ecs:RunTask` + `iam:PassRole`.
- **KMS everywhere**: ECR, S3, Secrets Manager, CloudWatch Logs, Step Functions data.
- **Private-only networking**: PrivateLink + VPC endpoints; no public IPs on tasks; security groups
  scoped to DB ports.
- **Image supply chain**: ECR scan-on-push (Inspector), immutable tags, signed images
  (optional), pull-through cache to avoid unvetted public pulls.
- **Auditability**: CloudTrail on all control-plane calls; Step Functions history retained 90 days;
  verdicts retained in S3 per policy.
- **IaC**: define everything in **Terraform/CDK** so the stack is reviewable and reproducible (an
  enterprise non-negotiable, and itself an *ops-overhead reducer*).

### 5.11 Cost optimization (maps directly to NF2 "on-demand")

- **Fargate Spot for the worker tasks** — up to **~70% off**; these jobs are **interruption-tolerant**
  (idempotent, re-runnable). Handle the **2-minute SIGTERM** to checkpoint/exit cleanly; let Step
  Functions/Batch retry the interrupted pair. Keep a small **on-demand** fraction (e.g., 80/20) for
  capacity assurance if the daily window is tight.
- **Step Functions**: Standard bills per **state transition** — `ItemBatcher` (fewer, bigger
  children) and avoiding chatty inner states keep transition counts (and cost) down.
- **No NAT** (via VPC endpoints) removes NAT-gateway hourly + per-GB charges.
- **S3 lifecycle** + **Athena** (pay-per-scan, partitioned) keep storage/analytics cheap.
- **Right-size** task CPU/memory to the real query profile; don't over-provision "just in case."
- **Idle cost ≈ $0**: nothing runs (or bills) between the daily/ad-hoc executions — exactly the
  "jobs run inconsistently → on-demand" requirement.

---

## 6. Why this satisfies every line of the brief

| Brief requirement | How it's met |
|---|---|
| Shift entire workflow to AWS | All components are AWS-native (ECR, CodeBuild, SFN, ECS Fargate, S3, Secrets Manager, EventBridge). |
| Build Python base image + deps, push to registry | CodeBuild multi-stage build → **ECR** (Harbor optional, §5.1). |
| Step Functions because runtime is high | **SFN Standard** (up to 1 yr) with `runTask.sync` waits on long tasks. |
| Run image on "clusters" / containers | **ECS Fargate** runs the custom image; serverless "cluster." |
| Automated, daily | **EventBridge Scheduler** cron. |
| Minimal operational overhead | Serverless end-to-end; no servers/clusters to patch. |
| Jobs spiky → on-demand pricing | Fargate + SFN + Athena are pay-per-use; **Fargate Spot** for the tasks. |
| Latency & speed prioritized | **Distributed Map** parallel fan-out; each task does an in-memory **pandas DataFrame** compare. |
| Don't overload sources | `MaxConcurrency` throttle + Scheduler flexible-window jitter. |
| Open to monolithic | One image / one `reconciler.py` codebase (modular monolith) on a distributed fabric. |
| Multiple architectures + tradeoffs | §8 (Variants A–D) + tradeoff matrix. |

---

## 7. Reference snippets

**7.1 Distributed Map state (ASL, JSONPath) — fan out query-pairs to Fargate**

```json
{
  "ReconcileAllPairs": {
    "Type": "Map",
    "ItemReader": {
      "Resource": "arn:aws:states:::s3:getObject",
      "ReaderConfig": { "InputType": "CSV", "CSVHeaderLocation": "FIRST_ROW" },
      "Parameters": { "Bucket": "recon-manifests", "Key.$": "$.manifestKey" }
    },
    "ItemBatcher": { "MaxItemsPerBatch": 25 },
    "MaxConcurrency": 30,
    "ToleratedFailurePercentage": 5,
    "TimeoutSeconds": 86400,
    "ItemProcessor": {
      "ProcessorConfig": { "Mode": "DISTRIBUTED", "ExecutionType": "STANDARD" },
      "StartAt": "RunReconcilerTask",
      "States": {
        "RunReconcilerTask": {
          "Type": "Task",
          "Resource": "arn:aws:states:::ecs:runTask.sync",
          "Parameters": {
            "Cluster": "arn:aws:ecs:REGION:ACCT:cluster/recon",
            "TaskDefinition": "recon-task",
            "LaunchType": "FARGATE",
            "NetworkConfiguration": {
              "AwsvpcConfiguration": {
                "Subnets": ["subnet-private-a", "subnet-private-b"],
                "SecurityGroups": ["sg-recon"],
                "AssignPublicIp": "DISABLED"
              }
            },
            "Overrides": {
              "ContainerOverrides": [{
                "Name": "reconciler",
                "Environment": [{ "Name": "BATCH_ITEMS_JSON", "Value.$": "States.JsonToString($.Items)" }]
              }]
            }
          },
          "Retry": [{
            "ErrorEquals": ["States.TaskFailed", "ECS.AmazonECSException"],
            "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0
          }],
          "End": true
        }
      }
    },
    "ResultWriter": {
      "Resource": "arn:aws:states:::s3:putObject",
      "Parameters": { "Bucket": "recon-results", "Prefix.$": "$.runDatePrefix" }
    },
    "Next": "AggregateAndReport"
  }
}
```

**7.2 `buildspec.yml` — build the custom Python image and push to ECR**

```yaml
version: 0.2
phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_URI
      - IMAGE_TAG=${CODEBUILD_RESOLVED_SOURCE_VERSION:0:12}
  build:
    commands:
      - docker build -t $ECR_URI/python-reconciler:$IMAGE_TAG .   # multi-stage Dockerfile, deps baked in
  post_build:
    commands:
      - docker push $ECR_URI/python-reconciler:$IMAGE_TAG
      - printf '{"ImageTag":"%s"}' "$IMAGE_TAG" > imageDetail.json
artifacts:
  files: [imageDetail.json]
```

**7.3 EventBridge Scheduler → Step Functions (Terraform sketch)**

```hcl
resource "aws_scheduler_schedule" "daily_recon" {
  schedule_expression          = "cron(0 2 * * ? *)"   # 02:00 daily
  schedule_expression_timezone = "Asia/Kolkata"
  flexible_time_window { mode = "FLEXIBLE", maximum_window_in_minutes = 15 }  # jitter DB load
  target {
    arn      = aws_sfn_state_machine.recon.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ manifestKey = "today/pairs.csv", runDatePrefix = "dt=${formatdate("YYYY-MM-DD", timestamp())}/" })
    retry_policy { maximum_retry_attempts = 5 }
  }
}
```

---

## 8. Alternative architectures & when to choose them

| Variant | What it is | Choose when | Tradeoffs |
|---|---|---|---|
| **A. SFN Distributed Map → ECS Fargate `runTask.sync`** *(recommended)* | Step Functions fans out one Fargate task per batch of pairs. | Default. Many pairs, long runtimes, want lowest ops overhead + native throttle. | Fargate cold start (tens of sec) per task → amortize with `ItemBatcher`. SFN transition cost on huge fan-outs (mitigated by batching). |
| **B. SFN → AWS Batch on Fargate (`submitJob.sync`)** | Step Functions submits to a managed **Batch** job queue; Batch handles array jobs, retries, priority, fair-share. | You think in "jobs," want **array jobs**, **job dependencies**, **priorities**, or richer built-in retry/queueing. Also good if many teams share a compute environment. | More moving parts (compute env + queue + job def). Distributed Map vs Batch array-jobs overlap — don't build both fan-out mechanisms. Batch on Fargate is **ECS-orchestrated only** (no EKS). |
| **C. Monolithic single Fargate task** | One big Fargate task runs the whole `reconciler.py`, looping all pairs internally. SFN just starts/monitors it. | **N is small** (≤ ~10–20 pairs) or runtimes short; you want the **simplest** thing. Honors brief's "open to monolithic" literally. | Serial → **slow** for large N (fails NF3). One failure can restart everything (add internal checkpointing). Bounded by one Fargate task's 16 vCPU / 120 GB / 200 GB ephemeral. |
| **D. EKS (or Batch-on-EKS)** | Run the image as K8s Jobs on an EKS cluster; SFN/Argo orchestrates. | Org already standardizes on **Kubernetes** and/or **Harbor+K8s**; need advanced scheduling, GPUs, or to reuse existing platform tooling. | **Highest ops overhead** (cluster, nodes, upgrades, autoscaler, add-ons) — directly opposes NF1. Idle node cost opposes NF2. Justify only with an existing K8s investment. |

**Evolution path:** start at **C** (prove the reconciliation logic in one container), then graduate to
**A** for scale/speed. Move to **B** only if you outgrow Distributed Map's queueing/priority model,
or to **D** only if a K8s mandate forces it.

---

## 9. Implementation playbook (build order)

1. **App first.** Write/port `reconciler.py` to (a) read a batch of pairs from an env var/S3 key,
   (b) pull creds from Secrets Manager, (c) connect to Oracle + Snowflake, (d) load both result sets
   into **pandas DataFrames** and compare (§5.6), (e) write a verdict JSON to S3. Make it **idempotent**.
2. **Containerize.** Multi-stage `Dockerfile` on a `python:3.x-slim` base (via ECR pull-through
   cache); bake in `oracledb`, `snowflake-connector-python`, `pandas`/`pyarrow`.
3. **Registry + CI.** Create the **ECR** repo (KMS, scan-on-push, lifecycle, immutable tags); wire
   **CodePipeline + CodeBuild** (§7.2). Confirm a scanned image lands in ECR.
4. **Networking.** VPC with **private subnets** (≥2 AZs); **VPC endpoints** (ECR, S3, Logs, Secrets,
   SFN, STS); **Snowflake PrivateLink**; **Direct Connect/VPN + Route 53 Resolver** to on-prem
   Oracle; security groups for 1521 / 443.
5. **Secrets.** Store + rotate Oracle and Snowflake credentials in Secrets Manager.
6. **Compute.** ECS cluster + **Fargate task definition** referencing the ECR image; task role +
   execution role (least privilege).
7. **Orchestration.** Step Functions **Standard** state machine: Prep → **Distributed Map** (§7.1) →
   Aggregate (Athena/Lambda) → Notify (SNS). Set `MaxConcurrency`, `ItemBatcher`,
   `ToleratedFailure*`, `TimeoutSeconds`.
8. **Schedule.** **EventBridge Scheduler** daily cron with flexible window (§7.3).
9. **Reporting.** Glue table over the S3 verdicts; Athena saved queries; SNS subscriptions;
   (optional) QuickSight.
10. **Observability & cost.** CloudWatch alarms, dashboards, log retention; enable **Fargate Spot**
    with SIGTERM handling; verify NAT-free networking.
11. **IaC + review.** Capture all of the above in Terraform/CDK; security review (IAM, KMS, SG, data
    retention); run a load test that confirms `MaxConcurrency` protects the source DBs.

---

## 10. Use-this-skill triggers

Invoke this skill when a task involves **any** of:
- "Run the same/equivalent query on two databases and check the results match" (data reconciliation,
  migration validation, Oracle→Snowflake parity testing, ETL output verification).
- A **containerized Python batch job** that must move to AWS, run on a **schedule**, and be
  orchestrated by **Step Functions**.
- **Fanning out** many long-running DB/compute jobs in parallel **while throttling** a shared
  downstream (database, API) — i.e., Distributed Map + `MaxConcurrency`.
- Designing for **minimal operational overhead + on-demand pricing** with **custom images**
  (ECR/Harbor) and **private connectivity** to SaaS (PrivateLink) and on-prem (Direct Connect/VPN).

---

## 11. Detailed cost model & per-scenario pricing

All figures are **us-east-1 list prices** (Linux/x86), **30 runs/month**, and **exclude database-side
cost** (Snowflake warehouse credits, Oracle compute/licensing) — those are billed by Snowflake/Oracle,
not AWS, and are often the *largest real cost* (see insight #8). Numbers are derived from the unit
prices in §11.1; always re-validate in the **AWS Pricing Calculator** for your region before quoting.

### 11.1 Unit prices used

| Service | Unit price (us-east-1) |
|---|---|
| Fargate vCPU | **$0.04048 / vCPU-hour** |
| Fargate memory | **$0.004445 / GB-hour** |
| Fargate **Spot** | **~70% off** on-demand (≈ ×0.30; varies with capacity) |
| Step Functions **Standard** | **$0.025 / 1,000 transitions**; first 4,000/mo free |
| AWS **Batch** | **No surcharge** — pay only the underlying Fargate/EC2 |
| **EKS** control plane | **$0.10 / hour** = **$73 / mo** per cluster (always-on) |
| VPC **interface endpoint** | **$0.01 / hr / AZ** + **$0.01 / GB** processed |
| VPC **gateway endpoint** (S3/DDB) | **Free** |
| **NAT** gateway | $0.045 / hr / AZ + **$0.045 / GB** |
| **Site-to-Site VPN** / **TGW** attachment | **$0.05 / hr** ≈ $36.50/mo each (TGW adds $0.02/GB) |
| **Direct Connect** 1 G dedicated port | ~$0.30/hr ≈ **$219/mo** (shared enterprise asset) |
| Secrets Manager | $0.40 / secret-mo |
| ECR storage | $0.10 / GB-mo |
| CloudWatch Logs | $0.50 / GB ingest + $0.03 / GB-mo store |
| EventBridge Scheduler | 14 M invocations/mo free |
| Athena | $5 / TB scanned · Glue Catalog free tier |

### 11.2 Workload model (adjust these to your reality)

| Parameter | **Small** | **Medium** | **Large** |
|---|---|---|---|
| Query-pairs `N` | 50 | 500 | 5,000 |
| Avg wall-clock / pair (mostly DB-bound wait) | 3 min | 5 min | 8 min |
| Data pulled / pair (both DBs, into DataFrames) | 20 MB | 100 MB | 300 MB |
| Fargate task size (memory-led, §5.6) | 1 vCPU / 4 GB | 2 vCPU / 16 GB | 4 vCPU / 30 GB |
| `ItemBatcher` K (pairs/task) | 5 | 10 | 10 |
| **Total task-hours / run** = `N × wall-clock` | **2.5 h** | **41.7 h** | **666.7 h** |
| **Data pulled / month** = `N × size × 30` | 30 GB | 1.5 TB | 45 TB |

> Key identity: **total task-hours = `N × avg wall-clock`** — independent of how many tasks you run
> in parallel. Parallelism (Distributed Map) changes *wall-clock*, not *Fargate cost*.

### 11.3 Fargate compute cost (the variable core)

`vCPU-hrs = task-hrs × vCPU`; `GB-hrs = task-hrs × GB`; cost = `vCPU-hrs×$0.04048 + GB-hrs×$0.004445`.

| Size | vCPU-hrs/run | GB-hrs/run | **$/run** | **$/mo on-demand** | **$/mo Spot (−70%)** |
|---|---|---|---|---|---|
| **Small** 1/4 | 2.5 | 10 | $0.146 | **$4.4** | **$1.3** |
| **Medium** 2/16 | 83.3 | 666.7 | $6.34 | **$190** | **$57** |
| **Large** 4/30 | 2,666.7 | 20,000 | $196.9 | **$5,906** | **$1,772** |

Note how DataFrame memory inflates cost: at 16–30 GB, **memory is 45–47%** of the Fargate bill
(it would be ~10% at a 2 GB checksum-style task).

### 11.4 Orchestration (Step Functions Standard) cost

Transitions/run ≈ `(N/K children × 2) + ~6 parent`. With batching this stays in/near the free tier:

| Size | children/run | transitions/mo | **$/mo** |
|---|---|---|---|
| Small | 10 | 780 | **$0** (free tier) |
| Medium | 50 | 3,180 | **$0** (free tier) |
| Large | 500 | 30,180 | **$0.65** |

Unbatched (K=1) Large would be ~$7.40/mo — still negligible, but it shows why **`ItemBatcher` is a
cost lever, not just a performance one.** Step Functions is effectively a rounding error here.

### 11.5 Shared platform floor (same for every variant, fixed regardless of `N`)

This always-on, private-networking baseline is what you pay even on a day with **zero** runs:

| Item | Monthly |
|---|---|
| 6 interface endpoints (ECR-api, ECR-dkr, Secrets, Logs, STS, SFN) × 2 AZ | $87.60 |
| Snowflake PrivateLink interface endpoint × 2 AZ | $14.60 |
| On-prem Oracle path: Site-to-Site VPN **or** TGW attachment | $36.50 |
| Secrets Manager (2 secrets) + ECR storage + CodeBuild + S3/Athena/SNS | ~$3.10 |
| **Platform floor subtotal** | **≈ $141.80 / mo** |

*(Direct Connect, if used instead of VPN, adds ~$219/mo for the port — but it is normally a shared
enterprise asset amortized across many workloads, so attribute only marginal data transfer here.)*

### 11.6 Data transfer (a new line item caused by the DataFrame pull)

Pulling both full result sets crosses paid links: Snowflake via PrivateLink endpoint (**$0.01/GB**)
and on-prem Oracle via TGW (**$0.02/GB**), blended ≈ **$0.015/GB**.

| Size | Data/mo | **$/mo** |
|---|---|---|
| Small | 30 GB | $0.45 |
| Medium | 1.5 TB | $22.5 |
| Large | 45 TB | **$675** |

> **This line is ~$0 with a checksum pre-filter** (§5.6) when result sets match — at Large it's the
> single biggest avoidable cost after compute. CloudWatch Logs add ~$0.30 / $1.65 / $16 per mo.

### 11.7 All-in monthly totals per scenario

`Total = floor ($141.80) + CW Logs + data transfer + compute (+ EKS control plane for D)`:

| Scenario | **Small** (OD / Spot) | **Medium** (OD / Spot) | **Large** (OD / Spot) |
|---|---|---|---|
| **A — SFN Distributed Map → ECS Fargate** *(recommended)* | **$147 / $144** | **$356 / $223** | **$6,738 / $2,604** |
| **B — SFN → AWS Batch on Fargate** | ≈ A (no surcharge) | ≈ A | ≈ A |
| **C — Monolith single Fargate task** | **$148 (OD only)** | ✗ infeasible¹ | ✗ infeasible¹ |
| **D — EKS + Fargate** | $220 / $217 | $429 / $296 | $6,811 / $2,677 |
| **D — EKS + EC2 (Karpenter)** | ✗ worse² | ✗ worse² | **~$5,946 / ~$2,426** |

¹ *Monolith is serial: Medium = 41.7 h, Large = 666 h of single-task wall-clock — both blow the daily
window. Cost would equal A's compute, but it's infeasible on time, and a single long task can't use
Spot safely (one interruption = full restart). Use only for Small.*

² *EKS adds a fixed **$73/mo** control plane. At Small/Medium that overhead exceeds the EC2 compute
savings, so D is **more** expensive than A. EC2 only breaks even at **sustained Large** scale — and
even then it beats Fargate-Spot by only ~$180/mo while adding cluster/node ops the dollar model
doesn't capture.*

### 11.8 What the numbers say (key insights)

1. **The fixed networking floor (~$142/mo) dominates Small/Medium.** Small compute is ~$4/mo — the
   floor is **~30×** larger. For spiky low-volume jobs, optimize the *floor* (share endpoints/TGW/DX
   across workloads, drop non-essential endpoints), not the compute.
2. **The DataFrame choice adds two costs the checksum approach wouldn't:** (a) **memory** — 16–30 GB
   tasks make RAM ~45% of the Fargate bill; (b) **data transfer** — $22.5/mo (Medium) → $675/mo
   (Large). A **checksum pre-filter (§5.6)** collapses both toward zero when sets match → it's the
   #1 cost lever at scale.
3. **Parallelism is free on Fargate.** A and the monolith C use the *same* vCPU/GB-hours → same $.
   Distributed Map buys **speed and fault-isolation**, not a higher bill. Choose A over C for those,
   not cost.
4. **Fargate Spot (−70%) is the top compute lever:** Medium $190→$57, Large $5.9k→$1.8k. Safe here
   because tasks are idempotent and retryable; the monolith can't exploit it.
5. **Step Functions is a rounding error** (≤ $0.65/mo even at 5,000 pairs). Don't optimize it;
   just batch with `ItemBatcher`.
6. **AWS Batch adds no surcharge** → B ≈ A on cost. Pick B for queue/array/priority features only.
7. **EKS is the most expensive option here** until sustained Large scale — the $73/mo control plane
   is pure overhead for a daily batch job. This is the cost evidence behind the §8 "Variant D only
   if K8s is already your standard" guidance.
8. **Not in the AWS bill, but real:** Snowflake **warehouse credits** and Oracle compute. Pulling
   full result sets (DataFrame approach) keeps the Snowflake warehouse busy longer than a pushdown
   checksum would, *raising credit burn*. Model this separately with your Snowflake rate — it can
   dwarf the entire AWS bill.

### 11.9 Cost levers, ranked by impact

1. **Add the checksum pre-filter** to the DataFrame path → kills the data-transfer line and shrinks
   tasks when sets match (insight #2).
2. **Fargate Spot** for all worker tasks (−70% compute).
3. **Right-size memory** to the true largest pair; **chunk** instead of over-provisioning.
4. **Share the networking floor** (endpoints, TGW, DX) across accounts/workloads; use the **free S3
   gateway endpoint**; consider **single-AZ** endpoints in non-prod.
5. **`ItemBatcher`** to keep task-startup overhead and SFN transitions down.
6. **S3 lifecycle → Glacier** for old verdicts; partition Athena scans by run-date.
7. **Compute Savings Plans** *only* if you ever move to a steady baseline — not applicable to the
   spiky on-demand pattern the brief describes.

---

## 12. Sources

**Step Functions Distributed Map & workflow types**
- [Map state in Distributed mode — AWS docs](https://docs.aws.amazon.com/step-functions/latest/dg/state-map-distributed.html)
- [Step Functions Distributed Map — AWS News Blog](https://aws.amazon.com/blogs/aws/step-functions-distributed-map-a-serverless-solution-for-large-scale-parallel-data-processing/)
- [Distributed Map best practices — DEV](https://dev.to/aws-builders/step-functions-distributed-map-best-practices-for-large-scale-batch-workloads-55n2)
- [Choosing workflow type (Standard vs Express) — AWS docs](https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html)
- [Building cost-effective Step Functions workflows — AWS Compute Blog](https://aws.amazon.com/blogs/compute/building-cost-effective-aws-step-functions-workflows/)

**Compute (ECS Fargate / Batch / container choice)**
- [Run ECS/Fargate tasks with Step Functions (`runTask.sync`) — AWS docs](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html)
- [Choosing an AWS container service — AWS decision guide](https://docs.aws.amazon.com/decision-guides/latest/containers-on-aws-how-to-choose/choosing-aws-container-service.html)
- [AWS Batch Fargate compute environments — AWS docs](https://docs.aws.amazon.com/batch/latest/userguide/fargate.html)
- [AWS Batch FAQs](https://aws.amazon.com/batch/faqs/)

**Registry (ECR / Harbor)**
- [ECR pull-through cache — AWS docs](https://docs.aws.amazon.com/AmazonECR/latest/userguide/pull-through-cache.html)
- [Announcing ECR pull-through cache — AWS Blog](https://aws.amazon.com/blogs/aws/announcing-pull-through-cache-repositories-for-amazon-elastic-container-registry/)
- [Harbor use cases — EKS Anywhere](https://anywhere.eks.amazonaws.com/docs/packages/harbor/harboruse/)

**Build / CI**
- [Publish a Docker image to ECR with CodeBuild — AWS docs](https://docs.aws.amazon.com/codebuild/latest/userguide/sample-docker.html)
- [Build & push to ECR with CodePipeline (V2) — AWS docs](https://docs.aws.amazon.com/codepipeline/latest/userguide/tutorials-ecr-build-publish.html)

**Scheduling**
- [EventBridge Scheduler → start a Step Functions execution — AWS docs](https://docs.aws.amazon.com/step-functions/latest/dg/using-eventbridge-scheduler.html)
- [Scheduling ECS tasks: Scheduler vs Rules vs Step Functions — AWS Builder Center](https://builder.aws.com/content/34jTIHSM6RwweiUIMU4GJcgSVRs/how-to-schedule-ecs-tasks-choosing-between-eventbridge-scheduler-eventbridge-rules-and-step-functions)

**Connectivity & secrets**
- [AWS PrivateLink and Snowflake — Snowflake docs](https://docs.snowflake.com/en/user-guide/admin-security-privatelink)
- [Manage private connectivity endpoints (AWS) — Snowflake docs](https://docs.snowflake.com/en/user-guide/private-manage-endpoints-aws)
- [Site-to-Site VPN PSK with Secrets Manager — AWS Networking Blog](https://aws.amazon.com/blogs/networking-and-content-delivery/aws-site-to-site-vpn-secure-pre-shared-key-psk-management-with-aws-secrets-manager/)
- [VPC endpoints for Secrets Manager — AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/secure-sensitive-data-secrets-manager-terraform/vpc-endpoints.html)

**Cost / Spot**
- [AWS Fargate pricing](https://aws.amazon.com/fargate/pricing/)
- [AWS Batch cost optimization — nOps](https://www.nops.io/blog/aws-batch-cost-optimization/)

**Reconciliation technique**
- [data-diff technical explanation (divide-and-conquer checksums)](https://data-diff.readthedocs.io/en/latest/technical-explanation.html)
- [Data reconciliation best practices — Datafold](https://www.datafold.com/blog/data-reconciliation-best-practices/)
