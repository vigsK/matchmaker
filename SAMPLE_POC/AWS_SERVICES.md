# AWS Services Used — In-Depth Reference

This document lists **every AWS service provisioned by this project** (derived
directly from the Terraform in `infra/*.tf`) and explains, in depth, *what each
service does* and *why it is used here specifically*.

> **Reality check / correction:** `ARCHITECTURE.md` describes the two databases
> as *Aurora Serverless v2*. The Terraform that actually runs
> (`infra/rds.tf`) provisions **standard Amazon RDS instances**
> (`aws_db_instance`, `engine = "postgres"` and `engine = "mysql"`), not Aurora.
> This document describes the **real, deployed infrastructure**. Where it matters
> for scaling, the difference is called out.

---

## At-a-glance: service inventory

| # | Service | Terraform resource(s) | Role in this project |
|---|---------|------------------------|----------------------|
| 1 | **Amazon VPC** | `aws_vpc`, `aws_subnet`, `aws_route_table*` | Private network isolating DBs + compute |
| 2 | **Internet Gateway** | `aws_internet_gateway` | Public-subnet egress/ingress to internet |
| 3 | **NAT Gateway + EIP** | `aws_nat_gateway`, `aws_eip` | Outbound internet for private subnets |
| 4 | **VPC Endpoint (S3 Gateway)** | `aws_vpc_endpoint` | Free, private S3 access off the NAT |
| 5 | **Security Groups** | `aws_security_group` | Stateful firewalls (DB reachable only from Fargate) |
| 6 | **Amazon RDS** | `aws_db_instance`, `aws_db_subnet_group` | The two relational engines being compared |
| 7 | **Amazon S3** | `aws_s3_bucket`, `aws_s3_object`, + 3 config resources | Build source, Excel input, per-run artifacts |
| 8 | **Amazon ECR** | `aws_ecr_repository`, `aws_ecr_lifecycle_policy` | Private registry for the custom image |
| 9 | **AWS CodeBuild** | `aws_codebuild_project` | Builds & pushes the Docker image in-account |
| 10 | **Amazon ECS (Fargate)** | `aws_ecs_cluster`, `aws_ecs_cluster_capacity_providers`, `aws_ecs_task_definition` | Serverless compute that runs the 4 app MODEs |
| 11 | **AWS Step Functions** | `aws_sfn_state_machine` | Orchestrates prep → compare(map) → aggregate |
| 12 | **EventBridge Scheduler** | `aws_scheduler_schedule` | Daily cron trigger of the state machine |
| 13 | **AWS Secrets Manager** | `aws_secretsmanager_secret*` | DB credentials, fetched at runtime |
| 14 | **Amazon SNS** | `aws_sns_topic`, `aws_sns_topic_subscription` | PASS/FAIL result notifications |
| 15 | **Amazon CloudWatch Logs** | `aws_cloudwatch_log_group` | Logs for ECS, Step Functions, CodeBuild |
| 16 | **AWS IAM** | `aws_iam_role`, `aws_iam_role_policy*`, `aws_iam_policy_document` | Least-privilege roles for every component |
| 17 | **AWS STS** *(implicit)* | `aws_caller_identity` data source | Resolves account ID for ARNs/naming |

---

## 1. Amazon VPC (Virtual Private Cloud)

**What it is:** A logically isolated virtual network you fully control — IP
range (CIDR), subnets, route tables, gateways.

**Use case here:** The VPC is the security boundary for the whole system. It is
built across **2 Availability Zones** with:
- **Public subnets** — host the single NAT Gateway (the only thing that needs a
  route to the internet).
- **Private subnets** — host the Fargate tasks *and* both RDS databases. Neither
  the databases nor the compute has a public IP.

This matters because the databases hold the data under test and must never be
internet-reachable; everything that touches them runs inside the same private
network.

## 2. Internet Gateway (IGW)

**What it is:** The VPC component that allows communication between resources in
public subnets and the internet.

**Use case here:** Attached to the VPC so the **NAT Gateway** (which lives in a
public subnet and has a public Elastic IP) can reach the internet. Private
resources never use the IGW directly.

## 3. NAT Gateway + Elastic IP (EIP)

**What it is:** A managed Network Address Translation service that lets resources
in **private** subnets make **outbound** connections to the internet while
remaining unreachable from the inbound side. It requires a static public IP — the
Elastic IP.

**Use case here:** Fargate tasks in the private subnets need outbound internet
for things the S3 endpoint can't cover — e.g. **pulling the image from ECR**
(ECR API/registry over the internet path), reaching the **Secrets Manager**,
**SNS**, and **Step Functions** public endpoints. A **single** NAT Gateway is
used (one per project, not per AZ) as a deliberate cost-vs-simplicity trade-off,
documented in `ARCHITECTURE.md §6`.

## 4. VPC Endpoint — S3 Gateway Endpoint

**What it is:** A *gateway* VPC endpoint adds a route so S3 traffic stays on the
AWS private network instead of traversing the NAT/internet. Gateway endpoints
(S3 and DynamoDB) are **free**.

**Use case here:** This app is extremely S3-heavy — `prep` writes `queries.json`,
every `compare` task reads queries and writes a result JSON, `aggregate` reads
all results and writes the report, and **CodeBuild pulls `source.zip` from S3**.
Routing all of that through the free Gateway endpoint (`network.tf:78`) keeps
bulk artifact traffic **off the metered NAT Gateway**, cutting cost and latency.

## 5. Security Groups

**What it is:** Stateful virtual firewalls attached to ENIs (tasks, DB
instances). Rules are allow-only; return traffic is automatically permitted.

**Use case here:** The core network least-privilege control:
- **DB security group:** inbound allowed **only** from the **Fargate security
  group** on the engine port (5432 for Postgres, 3306 for MySQL). No public
  access.
- **Fargate security group:** no inbound at all; egress to the DBs, the S3
  endpoint, and (via NAT) the AWS service endpoints.

This is what enforces "the database is reachable only by our application," at the
network layer.

## 6. Amazon RDS (Relational Database Service)

**What it is:** Managed relational databases — AWS handles provisioning,
patching, backups, and failover for engines like PostgreSQL, MySQL, MariaDB,
Oracle, SQL Server.

**Use case here:** RDS provides the **two relational systems being compared** —
the entire point of the POC. `infra/rds.tf` provisions:
- `aws_db_instance.pg` — **PostgreSQL** (stands in for the "Oracle" side).
- `aws_db_instance.mysql` — **MySQL** (stands in for the "Snowflake" side).

Both are encrypted, private-only, and placed in a **DB subnet group**
(`aws_db_subnet_group`) that pins them to the private subnets across the 2 AZs.
The app seeds **identical synthetic data** into both, then runs 20 logically
equivalent query pairs against each and compares the result sets.

> **Scaling note:** because these are fixed-size `aws_db_instance`s (not Aurora
> Serverless v2), scaling to millions of rows means raising `db_instance_class`
> and storage explicitly, and considering **read replicas** / Aurora for elastic
> capacity. The standard instance does not auto-scale ACUs.

## 7. Amazon S3 (Simple Storage Service)

**What it is:** Object storage — unlimited, durable (11 nines), key/value blobs
in buckets.

**Use case here:** S3 is the project's **central nervous system / message bus**.
A single bucket (`aws_s3_bucket.main`) holds:
- `build/source.zip` + `build/Dockerfile` — the **CodeBuild source** (the build
  context, uploaded by Terraform via `aws_s3_object.source_zip`).
- `input/queries.xlsx` — the **Excel workbook** of query pairs that `prep` reads.
- `runs/<run_id>/queries.json` — the Distributed Map's input manifest.
- `runs/<run_id>/results/<query_id>.json` — one verdict per query.
- `runs/<run_id>/map-output/` — Step Functions ResultWriter manifest.
- `runs/<run_id>/report.json` — the final aggregated report.

Hardened with three companion resources: **versioning** (`aws_s3_bucket_versioning`),
**default encryption** (`aws_s3_bucket_server_side_encryption_configuration`), and
a **public-access block** (`aws_s3_bucket_public_access_block`). Because each run
is namespaced under `runs/<run_id>/`, runs are isolated and idempotent.

## 8. Amazon ECR (Elastic Container Registry)

**What it is:** A private, managed Docker/OCI image registry integrated with IAM
and ECS.

**Use case here:** Stores the **one custom application image** (the "Harbor or
similar" requirement). `aws_ecr_repository.app` has **scan-on-push** enabled; an
`aws_ecr_lifecycle_policy` keeps only the **last 10 images** so old tags don't
accumulate. ECS Fargate pulls `:latest` (or a pinned tag) from here at task
launch.

## 9. AWS CodeBuild

**What it is:** A fully managed CI build service — it spins up a build container,
runs your `buildspec.yml`, and tears down. Pay per build-minute.

**Use case here:** Builds the Docker image **inside the AWS account** (no local
Docker / no external CI needed — key for "reproducible on any AWS console").
`aws_codebuild_project.image` runs in **privileged mode** (required to build
Docker images), pulls `source.zip` from S3, executes `buildspec.yml`
(ECR login → `docker build` → `docker push`), and emits `image.json`. Uses the
small compute tier (`BUILD_GENERAL1_SMALL`) with a 30-minute timeout.

## 10. Amazon ECS on AWS Fargate

**What it is:** ECS is the container orchestrator; **Fargate** is the serverless
launch type — you define a task, AWS runs the container with no EC2 hosts to
manage. You pay only for the vCPU/memory while a task runs.

**Use case here:** The **execution engine** for the application. There is exactly
**one cluster** (`aws_ecs_cluster`), wired to the **FARGATE** capacity provider
(`aws_ecs_cluster_capacity_providers`), and **one task definition**
(`aws_ecs_task_definition`) referencing the custom ECR image. The same task runs
all four MODEs — `MODE`/`RUN_ID`/`QUERY_ID` are injected per invocation via
**container overrides**:
- `seed` — launched manually (`scripts/seed.sh` → `aws ecs run-task`).
- `prep`, `compare`, `aggregate` — launched by Step Functions via
  `ecs:runTask.sync` (which blocks until the task finishes).

Fargate is chosen over Lambda because queries are **long-running** and the brief
mandates running the **custom image**; there is **no always-on compute** —
tasks exist only during a run.

## 11. AWS Step Functions

**What it is:** A serverless **orchestration** service. You define a state
machine (states, transitions, retries, error handling, parallelism) in Amazon
States Language; it coordinates other services durably.

**Use case here:** The **brain** of the workflow (`aws_sfn_state_machine`,
`statemachine.asl.json`), a STANDARD-type machine:
1. **Initialize** — sets `run_id = execution name`.
2. **PrepareQueries** — `ecs:runTask.sync` with `MODE=prep`.
3. **CompareAllQueries** — a **Distributed Map** (`Mode: DISTRIBUTED`): reads
   `queries.json`, fans out **one Fargate `compare` task per query**, with
   `MaxConcurrency` capping parallelism, `Retry` for transient errors, `Catch`
   so one bad query doesn't abort the batch (`ToleratedFailurePercentage: 100`),
   and a `ResultWriter` to S3.
4. **Aggregate** — `ecs:runTask.sync` with `MODE=aggregate`.
5. **Done**.

Distributed Map is the key choice: it provides **retries, batching, isolation,
and scale (thousands of queries)** for free, which is exactly the per-query
parallelism this workload wants.

## 12. Amazon EventBridge Scheduler

**What it is:** A serverless scheduler that triggers targets on a cron/rate
expression (a successor to CloudWatch Events scheduled rules, with more targets
and flexible time windows).

**Use case here:** `aws_scheduler_schedule` fires a **daily** `StartExecution`
on the state machine with empty input — automating the recurring consistency
check with no always-on component. It assumes a dedicated role scoped to just
`states:StartExecution` on the one machine.

## 13. AWS Secrets Manager

**What it is:** A managed secrets store with encryption, fine-grained IAM access,
and rotation support.

**Use case here:** Holds **DB credentials** — one secret per engine
(`aws_secretsmanager_secret` + `aws_secretsmanager_secret_version`), each a JSON
blob `{engine, host, port, username, password, dbname}`. The app reads them at
**runtime** via `config.get_creds()` using the **task IAM role** (cached
per-process with `lru_cache`). Nothing sensitive is baked into the image or the
task definition — credentials never touch the build artifacts.

## 14. Amazon SNS (Simple Notification Service)

**What it is:** Pub/sub messaging — publishers send to a topic; subscribers
(email, SQS, Lambda, HTTP) receive fan-out copies.

**Use case here:** The **results channel**. `aggregate` publishes the run's
PASS/FAIL summary to an SNS topic (`aws_sns_topic`); an optional email
subscription (`aws_sns_topic_subscription`) delivers it to an operator. SNS
publish failure is treated as **non-fatal** — the report is already durably in
S3.

## 15. Amazon CloudWatch Logs

**What it is:** Centralized log aggregation, storage, and querying.

**Use case here:** Every compute component streams logs to dedicated log groups
(`aws_cloudwatch_log_group`): **ECS** task output (the `print` statements in each
mode), **Step Functions** execution history (logging level `ALL`), and
**CodeBuild** build output. Retention is short (14 days, per `ARCHITECTURE.md`)
to keep a POC cheap. This is the primary debugging surface.

## 16. AWS IAM (Identity and Access Management)

**What it is:** The authorization layer — roles, policies, and trust
relationships that define *who/what can do what* on which resources.

**Use case here:** Enforces **least privilege** for every component
(`aws_iam_role`, `aws_iam_role_policy`, `aws_iam_role_policy_attachment`, and
`aws_iam_policy_document` to build the JSON):
- **ECS execution role** — pull from ECR + write logs.
- **ECS task role** — S3 on the one bucket, `GetSecretValue` on the two secrets,
  `sns:Publish` on the one topic.
- **CodeBuild role** — ECR auth/push, read `build/*` from S3, write logs.
- **Step Functions role** — `ecs:RunTask` on the task family, **scoped
  `PassRole`** (condition `iam:PassedToService = ecs-tasks`),
  `states:StartExecution`/`DescribeExecution` **on itself** (Distributed Map
  spawns child executions), S3 for ItemReader/ResultWriter, and SFN log
  delivery.
- **Scheduler role** — only `states:StartExecution` on the one machine.

A role↔state-machine dependency cycle is avoided by **computing the state-machine
ARN from its name** in `locals.tf`.

## 17. AWS STS (Security Token Service) — implicit

**What it is:** Issues temporary credentials and identity info; the SDK/CLI uses
it to resolve "who am I."

**Use case here:** The `aws_caller_identity` data source (backed by STS
`GetCallerIdentity`) resolves the **account ID** at plan time so resource names
and ARNs (ECR URI, IAM ARNs, the precomputed state-machine ARN) are derived from
the account/region — part of what makes the stack **reproducible in any account**
without hardcoding IDs. The `aws_region` and `aws_availability_zones` data
sources play the same role for region/AZ values.

---

## How the services fit together (lifecycle)

```
                       BUILD TIME (once / on code change)
  Terraform ─zip─► S3 (build/source.zip) ─► CodeBuild ─docker build─► ECR
                                                  │
                                          (logs → CloudWatch)

                       RUN TIME (daily, automated)
  EventBridge Scheduler ─StartExecution─► Step Functions
        │
        ├─ prep      (ECS Fargate) : S3 queries.xlsx ─► S3 queries.json
        ├─ compare×N (ECS Fargate, Distributed Map) :
        │       Secrets Manager ─creds─► RDS (PG + MySQL) ─► S3 results/*.json
        └─ aggregate (ECS Fargate) : S3 results/* ─► S3 report.json ─► SNS ─► email

  Throughout:  VPC + Subnets + SG isolate everything;
               NAT Gateway = outbound to ECR/Secrets/SNS;
               S3 Gateway Endpoint = free S3 path;
               IAM roles gate every call;  CloudWatch captures all logs.
```

**One-line mental model:** *S3 is the data bus, Step Functions is the conductor,
ECS Fargate is the muscle, RDS holds the data under test, and IAM + the VPC keep
the whole thing locked down.*
