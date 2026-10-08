# Cross-Engine Query Consistency POC — Deep Dive

A from-the-ground-up walkthrough of **what this system does, how every piece
fits together, and exactly what happens end-to-end** — both at deploy time and
at run time. Written to be read top-to-bottom for understanding.

> **Live status (this account):** deployed and verified in account
> `958312553089` (`us-east-1`, profile `sandbox1`). Last run
> `run-20260623-091636` → **PASS, 20/20 queries consistent**.

---

## 1. The problem, in one sentence

> "We have an Excel sheet full of big SQL queries. A Python script runs each one
> on **two different database engines** and checks the results are **identical**.
> Move that off a laptop and make it serverless on AWS."

The on-prem original was *Oracle vs Snowflake*. To keep the POC license-free and
reproducible, the two engines are emulated by two **Amazon Aurora** clusters:

| Brief        | This POC                       | Plays the role of      |
|--------------|--------------------------------|------------------------|
| Oracle       | Aurora **PostgreSQL**          | "system A"             |
| Snowflake    | Aurora **MySQL**               | "system B"             |
| Excel + script | One container image, 4 modes | prep / compare / aggregate / seed |
| "Harbor" registry | Amazon **ECR**            | private image registry |
| Orchestration | **Step Functions** Distributed Map | one parallel branch per query |

The deep insight of the design: **the same SQL question written in two dialects
must produce the same logical answer.** `TO_CHAR` vs `DATE_FORMAT`, `||` vs
`CONCAT`, `EXTRACT(YEAR…)` vs `YEAR()` — different syntax, identical result set.
This POC proves that automatically for 20 queries.

---

## 2. Component inventory (what got provisioned)

Everything is Terraform in `infra/`, one resource-group per file:

| File | Creates | Why it exists |
|------|---------|---------------|
| `network.tf` | VPC, 2 public + 2 private subnets, IGW, **1 NAT GW**, route tables, **S3 gateway endpoint** | Private compute with controlled egress |
| `security.tf` | 3 security groups (fargate, pg, mysql) | Only Fargate may reach the DB ports |
| `rds.tf` | 2 Aurora clusters + 1 instance each | The two engines under test |
| `secrets.tf` | 2 Secrets Manager secrets (+ random passwords) | DB creds, never in code/env |
| `s3.tf` | 1 bucket (versioned, encrypted, private) + source objects | Build input + all run artifacts |
| `ecr.tf` | 1 ECR repo (+ lifecycle policy) | Holds the app image |
| `codebuild.tf` | CodeBuild project | Builds the image **in-account** (no laptop Docker) |
| `ecs.tf` | ECS cluster + **one** Fargate task definition | The compute that runs all 4 modes |
| `iam.tf` | Roles for CodeBuild, ECS exec, ECS task, Step Functions, Scheduler | Least-privilege wiring |
| `stepfunctions.tf` + `statemachine.asl.json` | The STANDARD state machine | Orchestrates the whole run |
| `scheduler.tf` | EventBridge Scheduler (daily cron) | Hands-free daily execution |
| `sns.tf` | SNS topic | Publishes the PASS/FAIL summary |

Resource names are derived from `project` + account-id + region
(`qmatch-958312553089-us-east-1`), so two accounts never collide.

---

## 3. Architecture diagram

```
                      EventBridge Scheduler  (cron: daily, UTC)
                                │  states:StartExecution
                                ▼
┌──────────────────────  AWS Step Functions  (STANDARD state machine)  ──────────────────────┐
│                                                                                             │
│  ┌──────────┐   ┌───────────────┐   ┌──────────────────────────────────┐   ┌────────────┐  │
│  │Initialize│──▶│ PrepareQueries │──▶│      CompareAllQueries           │──▶│  Aggregate │  │
│  │ (Pass)   │   │  MODE=prep     │   │      (DISTRIBUTED MAP)           │   │ MODE=aggreg│  │
│  │ run_id = │   │  Fargate task  │   │  ItemReader: queries.json (S3)   │   │ Fargate    │  │
│  │ exec name│   │                │   │  for each query (MaxConcurrency):│   │ task       │  │
│  └──────────┘   └───────┬───────┘   │    ┌────────────────────────────┐│   └─────┬──────┘  │
│                         │           │    │ RunComparison  MODE=compare ││         │         │
│                         │           │    │  Fargate task               ││         │         │
│                         │           │    │   ├─ run PostgreSQL query ─┐ ││         │         │
│                         │           │    │   ├─ run MySQL query  ─────┘ ││         │         │
│                         │           │    │   ├─ compare (hash+diff)    ││         │         │
│                         │           │    │   └─ write results/<qid>.json││         │         │
│                         │           │    └────────────────────────────┘│         │         │
│                         │           │  ResultWriter: map-output/ (S3)   │         │         │
│                         │           └──────────────────────────────────┘         │         │
└─────────────────────────┼───────────────────────┬──────────────────────────────┼──────────┘
                          │ ecs:runTask.sync       │ GetSecretValue                │ sns:Publish
                          ▼                         ▼                               ▼
                ┌──────────────────┐      ┌──────────────────┐            ┌──────────────────┐
                │   ECS Fargate    │      │ Secrets Manager  │            │   SNS topic      │
                │  (app image from │◀─────│  qmatch/postgres │            │  qmatch-results  │
                │      ECR)        │ host │  qmatch/mysql    │            └──────────────────┘
                └───┬─────────┬────┘ user └──────────────────┘
       pull image   │         │ pass
                    │         │
   ┌────────────────▼──┐   ┌──▼──────────────────────────  VPC  ──────────────────────────────┐
   │       ECR         │   │  private subnets (no public IP)                                    │
   │   qmatch-app      │   │     │                          │                                   │
   └────────▲──────────┘   │     │ S3 Gateway Endpoint      │ NAT GW ──▶ IGW ──▶ Internet       │
            │              │     ▼ (queries/results/report) │           (ECR API, STS, SNS,      │
   ┌────────┴──────────┐   │   ┌─────┐                      │            Secrets Manager)        │
   │    CodeBuild      │   │   │ S3  │     ┌─────────────┐  ┌─────────────┐                      │
   │ docker build+push │   │   │bucket│    │ Aurora      │  │ Aurora      │  ◀── Fargate only,   │
   └────────▲──────────┘   │   └─────┘     │ PostgreSQL  │  │ MySQL       │      via SG rules    │
            │ source.zip   │               │ (port 5432) │  │ (port 3306) │                      │
        ┌───┴───┐          │               └─────────────┘  └─────────────┘                      │
        │  S3   │          └────────────────────────────────────────────────────────────────────┘
        │bucket │
        └───────┘
```

**Two things to notice:**
- The S3 **gateway endpoint** means all the bulky artifact traffic
  (queries.json, per-query results, report.json) never touches the NAT
  Gateway — it's free and private.
- The NAT Gateway is only for **API egress**: pulling the image from ECR,
  fetching secrets, calling STS/SNS. That's why there's exactly one NAT GW (the
  main idle cost).

---

## 4. The single-image / four-modes design

There is **one** container image and **one** ECS task definition. What it does is
chosen at launch by the `MODE` environment variable. This is the cleanest part
of the design — read `src/app/main.py`:

```
MODE=seed       → seed.py       one-time: load identical data into both Auroras
MODE=prep       → prep.py       Excel → queries.json in S3      (SFN state 1)
MODE=compare    → compare.py    run ONE query on both, compare  (Map iteration)
MODE=aggregate  → aggregate.py  collect verdicts → report.json  (SFN final state)
```

`main.py` reads `MODE`, imports the matching module, calls its `run(cfg)`.
Supporting modules: `db.py` (SQLAlchemy engines), `config.py` (env → Config +
`get_creds` from Secrets Manager), `s3util.py` (get/put JSON), `comparison.py`
(the consistency algorithm), `queries.py` (the 20 query pairs — single source of
truth, also used to generate the Excel).

Why one image? Build once, push once, run four ways. No drift between "the seed
script" and "the compare script" — they share `comparison.py`, `db.py`,
canonicalisation, everything.

---

## 5. Deployment flow (what `make deploy` does, step by step)

This is the bring-up you just ran. Each step gates the next.

```
 1. make excel   scripts/gen_excel.py reads src/app/queries.py and writes
                 infra/seed/queries.xlsx  (20 rows: query_id, description,
                 category, postgres_sql, mysql_sql)
        │
        ▼
 2. make apply   terraform apply
                 • archive_file zips src/  → uploaded as build/source.zip in S3
                 • the queries.xlsx        → uploaded as input/queries.xlsx in S3
                 • all infra from §2 comes up
        │
        ▼
 3. make build   scripts/build_and_push.sh
                 • starts the CodeBuild project
                 • CodeBuild pulls source.zip, runs buildspec.yml:
                     - docker login to ECR
                     - docker build  (Dockerfile: python base + requirements)
                     - docker push   → ECR :latest
                 • script polls batch-get-builds until SUCCEEDED
        │
        ▼
 4. make seed    scripts/seed.sh
                 • aws ecs run-task with MODE=seed (one-off Fargate task)
                 • seed.py generates a deterministic dataset (Random(42)) and
                   inserts the SAME rows into BOTH clusters
                 • script polls describe-tasks until STOPPED, checks exitCode==0
        │
        ▼
 5. make run     scripts/run_pipeline.sh   →  see §6
```

The scripts never hardcode endpoints — they read `terraform output` (cluster
name, task-def ARN, subnets/SG, state-machine ARN, bucket). That's why a clean
state + apply is all that's needed when the account changes.

---

## 6. Runtime flow (one Step Functions execution)

`scripts/run_pipeline.sh` calls `StartExecution` with a name like
`run-20260623-091636` and an empty input `{}`. That name **becomes the run_id**
and the S3 prefix for everything in the run. The state machine
(`statemachine.asl.json`) then runs four states:

### State 1 — `Initialize` (Pass)
Sets `run_id = $$.Execution.Name`. No compute. This single value threads through
every later state and names every S3 object.

### State 2 — `PrepareQueries` (Task, `MODE=prep`)
Runs a Fargate task synchronously (`ecs:runTask.sync` — Step Functions blocks
until the task stops). `prep.py`:
- reads `input/queries.xlsx` from S3,
- loads it into a pandas DataFrame, validates required columns,
- writes a JSON array to **`runs/<run_id>/queries.json`**.

That file is the **work list** for the Map. Retries up to 2× on failure.

### State 3 — `CompareAllQueries` (Distributed Map)
The heart of the system.
- **ItemReader**: reads `runs/<run_id>/queries.json` from S3 → one item per query.
- **ItemSelector**: passes `{run_id, query_id}` into each iteration.
- **MaxConcurrency**: N iterations run in parallel (each in its own SFN child
  execution because mode is `DISTRIBUTED`).
- **ItemProcessor → `RunComparison`** (Task, `MODE=compare`): one Fargate task
  **per query**. `compare.py`:
  1. read this query's item from `queries.json`,
  2. run `postgres_sql` on the PG cluster, time it,
  3. run `mysql_sql` on the MySQL cluster, time it,
  4. `compare_result_sets(...)` (see §7),
  5. write the verdict to **`runs/<run_id>/results/<query_id>.json`**.
- **Error handling**: a *connection/SQL* failure exits non-zero → SFN `Retry`,
  then `Catch` routes to a `ComparisonFailed` Pass so the map as a whole still
  succeeds. An **inconsistent result is NOT a failure** — it exits 0; "the
  engines disagree" is a valid business finding, recorded in the result file.
- **ToleratedFailurePercentage: 100** — the map never aborts the run; the
  report is always produced.
- **ResultWriter**: writes Map metadata to `runs/<run_id>/map-output/`.

```
queries.json ──▶ ┌─ iteration Q01 ─ Fargate ─ PG + MySQL ─ compare ─▶ results/Q01.json ─┐
                 ├─ iteration Q02 ─ Fargate ─ PG + MySQL ─ compare ─▶ results/Q02.json ─┤
                 │                       … up to MaxConcurrency at once …               │
                 └─ iteration Q20 ─ Fargate ─ PG + MySQL ─ compare ─▶ results/Q20.json ─┘
```

### State 4 — `Aggregate` (Task, `MODE=aggregate`)
One Fargate task. `aggregate.py`:
- lists `runs/<run_id>/results/*.json`,
- tallies consistent / inconsistent / errored,
- `overall = PASS` iff there are **zero** inconsistent **and** zero errored,
- writes **`runs/<run_id>/report.json`**,
- publishes a summary to **SNS**.

### `Done` (Succeed)
The SFN execution succeeds. `run_pipeline.sh` then downloads and pretty-prints
`report.json`.

**S3 artifact tree for a run:**
```
s3://qmatch-958312553089-us-east-1/
└─ runs/<run_id>/
   ├─ queries.json            ← prep output (Map input)
   ├─ results/
   │  ├─ Q01_total_orders.json … Q20_distinct_country_segment.json
   ├─ map-output/             ← Distributed Map bookkeeping
   └─ report.json             ← final PASS/FAIL verdict
```

---

## 7. The consistency algorithm (`comparison.py`)

This is *why* the comparison is correct despite dialect differences and row
ordering. For each result set:

1. **Canonicalise every cell** (`canon_cell`) to an engine-agnostic string:
   - `NULL` → `∅` (a single unambiguous token),
   - numbers → fixed decimal, **rounded to 6 dp**, trailing zeros stripped, no
     scientific notation (so `1E+2` == `100`, and PG `NUMERIC` == MySQL
     `DECIMAL`),
   - `datetime` → ISO without microseconds, `date` → ISO, `bool` → `1`/`0`,
     `bytes` → hex.
2. **Canonicalise each row** = join its cells.
3. **Sort the rows** → makes the comparison **order-independent** (no `ORDER BY`
   needed for equality).
4. **SHA-256 fingerprint** the sorted rows → one hash per engine.
5. **Verdict**: `consistent` iff `pg_hash == mysql_hash` **and** row counts match.

On mismatch it computes a **multiset diff** (`Counter` subtraction) so the result
shows `only_in_postgres` / `only_in_mysql` rows (capped at 50/side) — you see
*exactly which rows* diverge, not just "they differ".

In your live run every pair matched, e.g. Q01:
`postgres_hash == mysql_hash == 96d981…636a`.

---

## 8. Data model & determinism (`seed.py`)

Three tables — `customers` (2,000; 200 deliberately have **no orders** so the
anti-join query Q10 is meaningful), `products` (200), `orders` (50,000 default).

The dataset is generated **once** in Python with a fixed seed (`Random(42)`),
then the **identical rows** are inserted into both engines. So before any query
runs the two databases are byte-for-byte equivalent — that's the control that
makes a *consistent* verdict trustworthy and an *inconsistent* one a real
dialect bug rather than a data difference.

The 20 queries (in `queries.py`) deliberately exercise cross-dialect hot spots:
counts/sums/avg, joins, group-by, `HAVING`, `LIMIT` with deterministic
tiebreaks, anti-joins, date formatting (`TO_CHAR`/`DATE_FORMAT`,
`EXTRACT`/`YEAR`), string concat (`||`/`CONCAT`), and `DISTINCT`.

---

## 9. Networking & security model

- **Aurora lives in private subnets**, no public IP. Only the Fargate security
  group can reach `5432`/`3306` (SG-to-SG rules in `security.tf`).
- **Fargate tasks have no public IP** (`AssignPublicIp: DISABLED`). They reach
  AWS APIs through the **NAT Gateway**, and reach **S3 through the gateway
  endpoint** (private, free).
- **No credentials in code or task env.** The task role calls
  `secretsmanager:GetSecretValue` at runtime (`get_creds` in `config.py`); the
  passwords are random (`random_password`) and only ever live in Secrets
  Manager. Aurora storage is encrypted (`storage_encrypted = true`).
- **S3 bucket** is private (public access block on), versioned, SSE-encrypted.

### IAM roles (`iam.tf`) — least privilege per actor
| Role | Used by | Can do |
|------|---------|--------|
| `qmatch-codebuild` | CodeBuild | pull source from S3, push to ECR, write logs |
| `qmatch-ecs-execution` | ECS agent | pull image from ECR, write logs |
| `qmatch-ecs-task` | the app container | read/write the S3 bucket, get the 2 secrets, publish SNS |
| `qmatch-sfn` | Step Functions | `ecs:RunTask`, pass the ECS roles, read/write S3 (Map I/O) |
| `qmatch-scheduler` | EventBridge Scheduler | `states:StartExecution` |

---

## 10. Sandbox-specific deviations (important)

This account is a restricted lab. Three changes were required to get a live run;
all are committed in the working tree:

1. **State reset.** The checked-in `terraform.tfstate` referenced a *previous*
   sandbox account (`767397692469`). It was backed up
   (`infra/terraform.tfstate.stale-767397692469.*.bak`) and discarded so a clean
   apply could build in `958312553089`.

2. **Aurora Serverless v2 is blocked by an org SCP.**
   `rds:CreateDBInstance` for instance class **`db.serverless`** is explicitly
   denied (SCP `p-cr6s9vs4`) — and an SCP explicit-deny cannot be overridden from
   inside the account. Provisioned classes *are* allowed, so:
   - `infra/variables.tf` gained `db_instance_class` (default `db.serverless`),
   - `infra/rds.tf` instances use `var.db_instance_class`,
   - apply runs with **`-var db_instance_class=db.t3.medium`**.
   > Trade-off: `db.t3.medium` does **not** scale to zero like Serverless v2, so
   > it bills continuously — run `make destroy` when finished. The clusters stay
   > `engine_mode = "provisioned"` either way; only the instance class changed.

3. **Docker Hub pull rate limit (429) in CodeBuild.** The base image was pulled
   anonymously from Docker Hub and got throttled. `src/Dockerfile` now pulls from
   the **ECR Public mirror**
   (`public.ecr.aws/docker/library/python:3.12-slim`) — no auth, no rate limit.

> In a normal (non-SCP) account, the original `db.serverless` default works and
> `make deploy` needs no `-var`.

---

## 11. Operating it

```bash
# full bring-up in this sandbox (note the -var):
make excel
cd infra && terraform apply -auto-approve -var db_instance_class=db.t3.medium && cd ..
make build
make seed

# run + verify:
make run        # starts one execution, prints report.json

# inspect artifacts:
aws s3 ls s3://qmatch-958312553089-us-east-1/runs/ --profile sandbox1

# tear everything down (S3 + ECR are force-emptied; t3 instances stop billing):
make destroy
```

`make run` is idempotent and cheap — run it as many times as you like; each gets
a fresh `run-<timestamp>` prefix. The EventBridge Scheduler also fires it daily
with no human involved.

---

## 12. Mental model / TL;DR

- **One image, four modes**, launched as Fargate tasks.
- **Step Functions** is the conductor; **Distributed Map** gives you "one
  parallel branch per query" for free, with retries and partial-failure
  tolerance.
- **S3 is the data bus** between stages (queries.json → results/*.json →
  report.json), reached privately via a gateway endpoint.
- **Correctness** rests on two ideas: (a) seed identical data into both engines,
  (b) compare results *order-independently* via canonicalise → sort → SHA-256.
- **Nothing runs on a laptop**: image built by CodeBuild, data generated and
  compared by Fargate, orchestrated by Step Functions, scheduled by EventBridge.
