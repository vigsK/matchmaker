# Cross-Engine Query Consistency POC (AWS, fully serverless)

Verifies that a set of large queries returns **identical results** across two
different relational database systems — the cloud-native replacement for an
on-prem "Excel of queries + a Python script that runs them on Oracle and
Snowflake and diffs the output".

The scenario is **Oracle vs Snowflake**. For a license-free, reproducible,
*serverless + on-demand* POC, the two systems are emulated with two **Amazon
Aurora Serverless v2** clusters:

| Brief        | This POC                        | Role                       |
|--------------|---------------------------------|----------------------------|
| Oracle       | Aurora **PostgreSQL** Serverless v2 | "system A"             |
| Snowflake    | Aurora **MySQL** Serverless v2      | "system B"             |
| Harbor       | Amazon **ECR**                  | private image registry     |
| Python script| Custom image on **ECS Fargate** | prep / compare / aggregate |
| Orchestration| **Step Functions** Distributed Map | one parallel branch/query |

Everything is serverless and on-demand: Aurora Serverless v2 (scales to
`min_acu` when idle), Fargate (no servers), Step Functions, EventBridge
Scheduler, CodeBuild, S3, SNS.

---

## Architecture

```
                           EventBridge Scheduler (daily, cron UTC)
                                        │ StartExecution
                                        ▼
┌─────────────────────────────  AWS Step Functions (STANDARD)  ───────────────────────────┐
│                                                                                          │
│  Initialize ──► PrepareQueries ──►  CompareAllQueries (DISTRIBUTED MAP) ──► Aggregate ──► │
│  (run_id =      (Fargate: prep)     ┌───────────────────────────────────┐   (Fargate:    │
│   exec name)     reads xlsx,        │ ItemReader: s3://…/queries.json    │    aggregate)  │
│                  writes             │ for each query (MaxConcurrency=N):  │    report.json │
│                  queries.json       │   RunComparison (Fargate: compare)  │   + SNS notify │
│                                     │     ├─ run PG  query  ┐             │                │
│                                     │     ├─ run MySQL query ┘ compare     │                │
│                                     │     └─ write results/<qid>.json     │                │
│                                     │ ResultWriter: s3://…/map-output/    │                │
│                                     └───────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────────────────────────────────┘
        │ ecs:runTask.sync                              ▲ GetSecretValue          │ SNS
        ▼                                               │                         ▼
   ECS Fargate (custom image from ECR)  ───────────►  Secrets Manager     SNS topic (results)
        │   ▲                                            (pg / mysql)
        │   │ pull image                                   ▲
        │   │                                              │ host/user/pass
        ▼   │                            ┌─────────────────┴─────────────────┐
  VPC private subnets ──► NAT GW ──► Internet (ECR/STS/SNS)                   │
        │  └─ S3 Gateway Endpoint ──► Amazon S3 (queries, results, reports)   │
        ▼                                                                     │
   Aurora Serverless v2:  PostgreSQL cluster   +   MySQL cluster  ◄───────────┘
        ▲
        │ docker build + push
   CodeBuild ◄── source.zip (Dockerfile + app) in S3   ──►  ECR repository
```

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for component-level detail and design
rationale.

---

## Repository layout

```
src/                     # build context (zipped to S3, built by CodeBuild)
  Dockerfile             # python:3.12-slim + deps
  buildspec.yml          # CodeBuild: build + push to ECR
  requirements.txt
  app/
    main.py              # entrypoint; dispatch on MODE
    queries.py           # 20 query pairs + schema (single source of truth)
    seed.py              # MODE=seed   - deterministic identical data into both DBs
    prep.py              # MODE=prep   - xlsx -> dataframe -> queries.json (S3)
    compare.py           # MODE=compare- run one query on both, compare, write result
    aggregate.py         # MODE=aggregate - collect verdicts -> report.json + SNS
    comparison.py        # order-independent hash + row-diff
    db.py / config.py / s3util.py
infra/                   # Terraform (one resource group per file)
  network.tf security.tf s3.tf secrets.tf rds.tf ecr.tf codebuild.tf
  iam.tf ecs.tf stepfunctions.tf statemachine.asl.json scheduler.tf sns.tf
  variables.tf outputs.tf locals.tf providers.tf versions.tf
  seed/queries.xlsx      # generated sample workbook (uploaded to S3)
scripts/                 # gen_excel.py, build_and_push.sh, seed.sh, run_pipeline.sh, destroy.sh
Makefile                 # one-command orchestration
```

---

## Prerequisites

- AWS credentials (this POC uses the `sandbox1` profile by default)
- Terraform >= 1.5, AWS CLI v2, Python 3, `make`
- **No local Docker needed** — the image is built in the cloud by CodeBuild.

---

## Deploy (one command)

```bash
make deploy            # excel + terraform apply + CodeBuild + seed
make run               # start one Step Functions run and print the report
```

For another account/region:

```bash
make deploy PROFILE=myprofile REGION=eu-west-1
```

### Or step by step

```bash
make excel             # regenerate infra/seed/queries.xlsx from queries.py
make apply             # provision VPC, Aurora x2, ECR, CodeBuild, ECS, SFN, ...
make build             # CodeBuild builds the image and pushes to ECR
make seed              # load identical synthetic data into both Aurora clusters
make run               # execute the consistency workflow, print report.json
```

---

## What "consistent" means

For every query the PostgreSQL and MySQL result sets are compared
**order-independently**:

1. each cell is canonicalised (NULLs, decimals rounded to 6 dp, dates, etc.),
2. rows are sorted and SHA-256 fingerprinted,
3. the verdict is `consistent` iff row counts **and** fingerprints match.

On a mismatch the per-query result includes a **row-level diff**
(`only_in_postgres` / `only_in_mysql`, capped at 50 rows) so you can see exactly
where the engines diverge. Results land in S3 and a summary is published to SNS:

```
s3://<bucket>/runs/<run_id>/queries.json      # prep output (Map ItemReader input)
s3://<bucket>/runs/<run_id>/results/<qid>.json # per-query verdict
s3://<bucket>/runs/<run_id>/report.json        # aggregated PASS/FAIL summary
```

---

## The 20 sample queries

The workbook (`infra/seed/queries.xlsx`, generated from `src/app/queries.py`)
has columns `query_id, description, category, postgres_sql, mysql_sql`. They
exercise counts, sums, joins, group-by, `HAVING`, `LIMIT`, anti-joins, date
functions (`TO_CHAR` vs `DATE_FORMAT`, `EXTRACT(YEAR…)` vs `YEAR()`), string
functions (`||` vs `CONCAT`), and `DISTINCT` — i.e. real cross-dialect
differences that must still produce identical *logical* output.

---

## Cost & teardown

Idle cost is dominated by the single NAT Gateway (~\$0.045/hr) and Aurora at
`min_acu` (0.5 ACU x2). To remove everything:

```bash
make destroy
```

S3 (`force_destroy`) and ECR (`force_delete`) are emptied automatically; secrets
use a 0-day recovery window so re-applies don't collide.

---

## Reproducibility

- All infrastructure is Terraform; engine versions, CIDRs, ACUs, schedule, and
  sizing are variables with sane defaults.
- The container image is built **in-account** by CodeBuild from source stored in
  S3 — no dependency on a developer laptop or an external registry.
- Resource names are derived from `project` + account id + region, so two
  accounts can run the stack side by side without clashes.
