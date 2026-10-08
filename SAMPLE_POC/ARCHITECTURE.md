# Architecture & Design Rationale

## 1. Problem → cloud mapping

On-prem today: an Excel workbook holds pairs of large queries; a Python script
runs each query against **Oracle** and **Snowflake** and checks the two result
sets match. We lift this to AWS with **minimal operational overhead**, **fully
serverless / on-demand** economics, and a heavy emphasis on **Step Functions**
because query runtimes are high and the work is naturally per-query parallel.

| Requirement | Decision |
|---|---|
| Two relational systems to compare | **Aurora Serverless v2** PostgreSQL + MySQL (license-free, serverless, reproducible in any account). Real Oracle/Snowflake would break "reproducible on any AWS console". |
| Custom Python image with deps | Built by **CodeBuild** from a Dockerfile, pushed to **ECR** (the "Harbor or similar" requirement). |
| High query runtime, per-query parallelism | **Step Functions Distributed Map**, one iteration per query, each running both engines via **ECS Fargate** `runTask.sync`. |
| Run daily, jobs inconsistent | **EventBridge Scheduler** (cron) → on-demand Fargate + Aurora that scale down when idle. |
| Low ops overhead, monolithic-friendly | **One** container image, **one** task definition, four runtime MODEs. No always-on compute. |
| Reproducible everywhere | 100% Terraform + in-account CodeBuild; names derived from account/region. |

## 2. Components

- **VPC** — 2 AZs; public subnets host a single NAT Gateway; private subnets host
  Fargate tasks and both Aurora clusters. A **free S3 Gateway endpoint** keeps
  bulk artifact traffic off the NAT.
- **Aurora Serverless v2 ×2** — PostgreSQL (port 5432) and MySQL (port 3306),
  `db.serverless` instances scaling `min_acu`→`max_acu`. Encrypted, private-only,
  reached only from the Fargate security group.
- **Secrets Manager** — one secret per engine: `{engine, host, port, username,
  password, dbname}`. The app reads them at runtime with the task role; nothing
  sensitive is in the image or task definition.
- **ECR** — single repository for the custom image; scan-on-push; lifecycle keeps
  the last 10 images; `force_delete` for clean teardown.
- **CodeBuild** — `privileged_mode` Linux container; source is `build/source.zip`
  in S3 (Dockerfile + buildspec + app); pulls `python:3.12-slim`, installs
  dependencies, builds, and pushes `:<tag>` and `:latest` to ECR.
- **ECS Fargate** — one cluster, one task definition (the custom image). Static
  config (bucket, secret ARNs, SNS topic, row count) is in the task definition;
  `MODE`/`RUN_ID`/`QUERY_ID` are injected per invocation via container overrides.
- **Step Functions (STANDARD)** — orchestration (see §3).
- **EventBridge Scheduler** — daily `StartExecution` with empty input.
- **SNS** — results topic (optional email subscription) carrying the PASS/FAIL
  summary.
- **CloudWatch Logs** — ECS, Step Functions (level `ALL`), CodeBuild.

## 3. Step Functions workflow (the core)

State machine `<project>-consistency`, type STANDARD:

1. **Initialize** (`Pass`) — sets `run_id = $$.Execution.Name`. Every artifact for
   the run lives under `s3://<bucket>/runs/<run_id>/`, making runs isolated and
   idempotent.
2. **PrepareQueries** (`ecs:runTask.sync`, `MODE=prep`) — reads the Excel from S3
   into a pandas DataFrame, validates columns, writes `runs/<run_id>/queries.json`
   (a JSON array). This is the Distributed Map's input.
3. **CompareAllQueries** (`Map`, `Mode: DISTRIBUTED`, `ExecutionType: STANDARD`):
   - **ItemReader** `s3:getObject` (`InputType: JSON`) reads `queries.json`.
   - **ItemSelector** projects each item to `{run_id, query_id}` (we pass only the
     id; the worker re-reads the full SQL from S3 so arbitrarily large queries
     never hit env-var size limits).
   - **ItemProcessor → RunComparison** (`ecs:runTask.sync`, `MODE=compare`) runs
     the PostgreSQL and MySQL variants, compares, and writes
     `runs/<run_id>/results/<query_id>.json`. `Retry` handles transient ECS/timeout
     errors; `Catch` routes hard failures to a `ComparisonFailed` marker so one bad
     query never aborts the whole batch (`ToleratedFailurePercentage: 100`). The
     worker persists an `error` result to S3 *before* failing, so it still appears
     in the report.
   - **MaxConcurrency** caps parallel Fargate tasks (default 10).
   - **ResultWriter** `s3:putObject` writes the map manifest to
     `runs/<run_id>/map-output/`.
4. **Aggregate** (`ecs:runTask.sync`, `MODE=aggregate`) — reads every
   `results/*.json`, computes totals + overall PASS/FAIL, writes
   `runs/<run_id>/report.json`, and publishes the summary to SNS.
5. **Done** (`Succeed`).

Why per-query Fargate tasks rather than Lambda: queries are long-running and the
brief mandates running the **custom image** on **ECS Fargate**. One image + four
MODEs keeps the system monolithic and low-overhead while Distributed Map provides
isolation, retries, batching, and scale (thousands of queries) for free.

## 4. Consistency algorithm

`comparison.compare_result_sets` is order-independent:

- canonicalise each cell — `NULL→∅`, `bool→0/1`, decimals/floats rounded to 6 dp
  with trailing zeros stripped (collapses engine precision differences),
  dates/timestamps to ISO, bytes to hex;
- sort the canonical rows and SHA-256 fingerprint them;
- **consistent** ⇔ equal row counts **and** equal fingerprints;
- on mismatch, a multiset diff (`Counter`) yields `only_in_postgres` /
  `only_in_mysql` (capped) for actionable debugging.

The SQL pairs additionally wrap aggregates in `ROUND(...,2/4)` and `CAST` so the
engines emit comparable types before normalisation — belt and suspenders.

## 5. Security & IAM (least privilege)

- **Network**: Aurora has no public access; inbound only from the Fargate SG on
  the engine port. Fargate has no inbound, egress via NAT/S3-endpoint.
- **ECS execution role**: `AmazonECSTaskExecutionRolePolicy` (ECR pull + logs).
- **ECS task role**: S3 on the one bucket, `GetSecretValue` on the two secrets,
  `sns:Publish` on the one topic.
- **CodeBuild role**: ECR auth + push to the one repo, read `build/*` from S3,
  CloudWatch Logs.
- **Step Functions role**: `ecs:RunTask` on the task family, scoped `PassRole`
  (condition `iam:PassedToService = ecs-tasks`), the managed `events` rule for
  `.sync`, `states:StartExecution`/`DescribeExecution` on **itself** (Distributed
  Map spawns child executions), S3 for ItemReader/ResultWriter, and the logs
  delivery permissions required for SFN logging.
- **Scheduler role**: `states:StartExecution` on the one state machine.

A dependency cycle (role policy ↔ state machine) is avoided by computing the
state-machine ARN from its known name in `locals.tf`.

## 6. Cost / operational posture

- No always-on compute. Aurora idles at `min_acu`; Fargate runs only during a job.
- Single NAT Gateway (cost vs. the operational simplicity of not managing ~6
  interface endpoints). The README documents the interface-endpoint alternative
  for a NAT-free footprint.
- `containerInsights` disabled, 14-day log retention — minimal overhead for a POC.

## 7. Extending to real Oracle / Snowflake

Swap the two Aurora clusters for the real systems:
- add a driver to `requirements.txt` (`oracledb`, `snowflake-sqlalchemy`),
- extend `db._url`/`config.DbCreds` with the new engine,
- point the secrets at the real endpoints.
The orchestration, Distributed Map, comparison, and reporting are unchanged.
```
