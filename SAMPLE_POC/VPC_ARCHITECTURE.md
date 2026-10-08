# VPC Architecture — Query-Consistency POC

This document explains the network I built for the POC, **resource by resource**, and the
reasoning behind each choice. Everything described here lives in
[`infra/network.tf`](infra/network.tf), [`infra/security.tf`](infra/security.tf),
[`infra/rds.tf`](infra/rds.tf), and the `awsvpcConfiguration` blocks in
[`infra/statemachine.asl.json`](infra/statemachine.asl.json).

The design goal is a **two-tier, multi-AZ VPC** where compute (Fargate) and data (RDS)
sit in *private* subnets with **no inbound access from the internet**, while still being
able to reach AWS APIs (ECR, Secrets Manager, SNS, S3) for the pipeline to function.

---

## 1. Topology at a glance

```
                              ┌──────────────────────────────────────────────────────────┐
                              │  VPC  qmatch-vpc   10.42.0.0/16   (DNS support+hostnames)  │
                              │                                                            │
   Internet                   │   ┌── PUBLIC TIER (rt-public → IGW) ──────────────────┐    │
      │                       │   │                                                    │   │
      │                       │   │  public-az-a  10.42.0.0/24    public-az-b 10.42.1.0/24 │
   ┌──┴───┐   attach          │   │  map_public_ip_on_launch = true                    │   │
   │ IGW  │───────────────────┤   │        │                                           │   │
   └──────┘                   │   │   ┌────┴─────┐                                      │   │
                              │   │   │ NAT GW   │ (+ Elastic IP)   ← only thing here   │   │
                              │   │   └────┬─────┘                                      │   │
                              │   └────────┼───────────────────────────────────────────┘   │
                              │            │ egress for private tier                        │
                              │   ┌────────┼── PRIVATE TIER (rt-private → NAT) ──────────┐  │
                              │   │        ▼                                              │ │
                              │   │  private-az-a 10.42.100.0/24   private-az-b 10.42.101.0/24
                              │   │                                                       │ │
                              │   │   ┌─────────────┐   ┌─────────────┐   ┌────────────┐  │ │
                              │   │   │ Fargate task│   │ RDS Postgres│   │ RDS MySQL  │  │ │
                              │   │   │  (sg-fargate)│  │  (sg-pg)    │   │ (sg-mysql) │  │ │
                              │   │   └─────────────┘   └─────────────┘   └────────────┘  │ │
                              │   │         │                                             │ │
                              │   └─────────┼─────────────────────────────────────────────┘ │
                              │             │ S3 traffic bypasses NAT                        │
                              │      ┌──────┴───────┐                                        │
                              │      │ S3 Gateway   │  (route-table entry, $0)               │
                              │      │  Endpoint    │                                        │
                              │      └──────────────┘                                        │
                              └──────────────────────────────────────────────────────────┘
```

---

## 2. The VPC itself

```hcl
resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"   # 65,536 addresses
  enable_dns_support   = true
  enable_dns_hostnames = true
}
```

- **`10.42.0.0/16`** — a deliberately *uncommon* private range. Picked over the usual
  `10.0.0.0/16` / `172.31.0.0/16` so this VPC won't collide if it's ever peered with a
  corporate network or the default VPC.
- **`enable_dns_support` + `enable_dns_hostnames`** — both are **required** for two things
  this POC depends on:
  1. **RDS endpoint resolution** — `qmatch-pg.xxxx.rds.amazonaws.com` only resolves to a
     private IP when DNS hostnames are on.
  2. **The S3 Gateway endpoint** — gateway endpoints rely on the VPC's DNS to swap the public
     S3 name for the prefix-list route. With DNS off, the endpoint silently does nothing.

---

## 3. Subnets — two tiers × two AZs

Subnet CIDRs are computed, not hand-written, via `cidrsubnet()` carving `/24`s out of the
`/16`:

| Subnet           | Terraform expression                      | CIDR             | AZ   | Tier    |
|------------------|-------------------------------------------|------------------|------|---------|
| `public[0]`      | `cidrsubnet(vpc_cidr, 8, 0)`              | `10.42.0.0/24`   | az-a | public  |
| `public[1]`      | `cidrsubnet(vpc_cidr, 8, 1)`              | `10.42.1.0/24`   | az-b | public  |
| `private[0]`     | `cidrsubnet(vpc_cidr, 8, 0 + 100)`        | `10.42.100.0/24` | az-a | private |
| `private[1]`     | `cidrsubnet(vpc_cidr, 8, 1 + 100)`        | `10.42.101.0/24` | az-b | private |

**Why I did it this way:**

- **`8` new bits** turns the `/16` into `/24`s — 256 addresses each, plenty for a POC and
  easy to read (`.0.x` = public, `.100.x` = private).
- **The `+ 100` offset** for private subnets is a readability trick: anything in the `100`+
  block is private *by inspection*, with a big numeric gap so the two tiers never overlap as
  AZ count grows.
- **`az_count = 2`** → subnets are spread across two Availability Zones. This is the
  minimum for AZ-fault tolerance and, importantly, **RDS requires a DB subnet group that
  spans ≥ 2 AZs** even for a single-AZ instance.
- **`map_public_ip_on_launch = true` on public subnets only** — anything launched there gets
  a public IP automatically. Private subnets deliberately omit this; nothing in them is
  internet-addressable.

> **Public subnets in this POC hold exactly one thing: the NAT Gateway.** No application
> runs in them. They exist purely to give the private tier a route out.

---

## 4. Internet Gateway + the public route table

```hcl
resource "aws_internet_gateway" "igw" { vpc_id = aws_vpc.main.id }

resource "aws_route_table" "public" {
  route { cidr_block = "0.0.0.0/0"  gateway_id = aws_internet_gateway.igw.id }
}
```

- The **IGW** is the VPC's door to the internet. By itself it does nothing — a subnet only
  becomes "public" when its route table sends `0.0.0.0/0` at the IGW *and* its instances
  have public IPs.
- The **public route table** does exactly that and is associated with both public subnets.
  This is what lets the NAT Gateway (which lives in `public[0]`) actually reach the internet.

---

## 5. NAT Gateway — the one-way valve for the private tier

```hcl
resource "aws_eip" "nat" { domain = "vpc" }

resource "aws_nat_gateway" "nat" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id   # NAT must live in a PUBLIC subnet
  depends_on    = [aws_internet_gateway.igw]
}

resource "aws_route_table" "private" {
  route { cidr_block = "0.0.0.0/0"  nat_gateway_id = aws_nat_gateway.nat.id }
}
```

This is the heart of the "private but functional" design:

- The NAT Gateway sits in **`public[0]`** and owns a static **Elastic IP**. It must be in a
  public subnet because *it* needs the IGW route to reach the internet.
- The **private route table** points `0.0.0.0/0` at the NAT (not the IGW). So private
  resources can **initiate outbound** connections (pull container images from ECR, fetch DB
  creds from Secrets Manager, publish to SNS) but the internet **cannot initiate inbound** —
  NAT only forwards return traffic for connections that started inside.
- **`depends_on = [igw]`** — a NAT Gateway is useless until the IGW is attached; the explicit
  dependency stops Terraform racing the two.

### Design trade-off I deliberately accepted: **single NAT, not one-per-AZ**

The "textbook" highly-available pattern is one NAT Gateway *per AZ* (so an AZ outage can't sever
the other AZ's egress). I chose a **single shared NAT** because:

- This is a throwaway POC; a brief egress outage during a rare AZ failure is acceptable.
- A second NAT Gateway is a second hourly charge **plus** a second Elastic IP — pure cost with
  no POC benefit.
- For production, you'd split this into `aws_nat_gateway` per-AZ with a private route table
  per-AZ. That's the one thing I'd change before calling this production-ready.

---

## 6. S3 Gateway Endpoint — the cost/perf optimization I added

```hcl
resource "aws_vpc_endpoint" "s3" {
  service_name      = "com.amazonaws.us-east-1.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
}
```

The pipeline reads the input Excel and writes result diffs to S3 — potentially a lot of bytes.
Without an endpoint, **all of that S3 traffic would route through the NAT Gateway**, which
charges a **per-GB data-processing fee** on top of the hourly rate.

A **Gateway endpoint** instead injects an AWS-managed prefix-list route into the private route
table, so S3-bound traffic goes **straight to S3 over the AWS backbone** — never touching the
NAT. It is:

- **Free** — gateway endpoints (S3 and DynamoDB only) have no hourly or data charge.
- **Faster** — stays on the AWS network, no NAT hop.
- **More secure** — S3 traffic never leaves AWS's private network.

> **Why gateway and not interface?** S3 and DynamoDB are the only two services that offer the
> *free* gateway-endpoint type. Everything else (ECR, Secrets Manager, SNS) would need
> **interface** endpoints, which are *paid* (hourly + per-GB). For a POC, paying for those isn't
> worth it — that lower-volume API traffic just rides the NAT. S3 is the one high-volume path,
> so it's the one I optimized.

---

## 7. Security Groups — the stateful firewall layer

Three SGs enforce **least-privilege, identity-based** rules. Note these reference each
**other** by SG ID, not by CIDR — so the rules keep working no matter what private IPs AWS
hands out.

```
   ┌────────────────┐  egress: ALL (0.0.0.0/0)      ┌──────────────────────┐
   │  sg-fargate    │  inbound: NONE                │  Internet (via NAT)  │
   │  (the task)    │ ─────────────────────────────▶│  ECR / Secrets / SNS │
   └───────┬────────┘                               └──────────────────────┘
           │ source = sg-fargate
           │  :5432              :3306
           ▼                       ▼
   ┌────────────────┐      ┌────────────────┐
   │  sg-pg         │      │  sg-mysql      │
   │  in: 5432 from │      │  in: 3306 from │
   │     sg-fargate │      │     sg-fargate │
   └────────────────┘      └────────────────┘
```

### `sg-fargate` — the compute SG
- **Inbound: none.** The task is a *batch worker*; nothing ever connects *to* it, so it needs
  zero ingress rules. This is the strongest possible posture.
- **Outbound: all.** It needs to reach a wide, AWS-managed set of endpoints (ECR image pulls,
  Secrets Manager, SNS, plus the two databases). Restricting egress here would mean chasing
  AWS service IP ranges, so "all egress" from a no-inbound task is the pragmatic choice.

### `sg-pg` (port 5432) and `sg-mysql` (port 3306) — the data SGs
- **Inbound is keyed to `sg-fargate`'s ID**, not an IP range. Translation: *"only something
  carrying the Fargate security group may open a database connection."* Even another resource
  sitting in the same private subnet **cannot** reach the databases unless it's in `sg-fargate`.
- This is **identity-based microsegmentation** and it's the single most important security
  control in the design — the databases are reachable by exactly one workload and nothing else.

---

## 8. How the data tier stays private (RDS)

```hcl
resource "aws_db_subnet_group" "main" {
  subnet_ids = aws_subnet.private[*].id          # both private subnets
}

resource "aws_db_instance" "pg" {            # (mysql is identical)
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.pg.id]
  publicly_accessible     = false
  multi_az                = false             # POC: single-AZ to save cost
}
```

- The **DB subnet group spans both private subnets** (RDS demands ≥ 2 AZs even when the
  instance is single-AZ).
- **`publicly_accessible = false`** means RDS gets *no* public DNS name or IP — it's only
  resolvable/reachable from inside the VPC.
- Combined with `sg-pg`/`sg-mysql`, the databases are reachable by **exactly one path**:
  a Fargate task in `sg-fargate`. There is no internet route to them at all.

> `multi_az = false`, `storage_encrypted = false`, and `backup_retention = 0` are
> **POC-only cost shortcuts** dictated by the Pluralsight sandbox (its SCP also blocks Aurora
> Serverless, which is why these are standard `db.t3.micro` instances). In production you'd
> flip all three on.

---

## 9. How the compute tier plugs into the network

Fargate tasks are launched (by Step Functions and by `aws ecs run-task`) with this network
config — see [`infra/statemachine.asl.json`](infra/statemachine.asl.json) and the
`run_task_network_config` output:

```json
"NetworkConfiguration": {
  "AwsvpcConfiguration": {
    "Subnets":        ["<private[0]>", "<private[1]>"],
    "SecurityGroups": ["<sg-fargate>"],
    "AssignPublicIp": "DISABLED"
  }
}
```

- **`Subnets` = the private subnets** → tasks get a private IP only.
- **`AssignPublicIp = DISABLED`** → no public IP, so the *only* way out is the NAT route.
- Because there's no public IP, a task **must** use the NAT to pull its own container image
  from ECR and to reach Secrets Manager. This is why the NAT Gateway is load-bearing: remove
  it and tasks can't even start.

---

## 10. End-to-end traffic walkthrough

Tracing one pipeline run proves every piece earns its place:

1. **Step Functions** runs a Fargate task into a **private subnet**, `sg-fargate`, no public IP.
2. Task pulls its **container image from ECR** → no public IP, so it routes
   `0.0.0.0/0` → **private route table** → **NAT Gateway** → IGW → ECR. ✅ NAT justified.
3. Task fetches DB credentials from **Secrets Manager** → same NAT path. ✅
4. Task reads the input Excel / writes diffs to **S3** → matches the **S3 Gateway endpoint**
   prefix-list route → straight to S3, **bypassing NAT**. ✅ Endpoint justified.
5. Task connects to **RDS Postgres:5432 / MySQL:3306** → allowed because the task carries
   `sg-fargate`, which is the *only* source `sg-pg`/`sg-mysql` permit. Traffic stays entirely
   inside the private subnets. ✅ SG identity rule justified.
6. Task publishes results to **SNS** → NAT path again. ✅
7. **Inbound from the internet at any step: impossible** — no public IPs on workloads, no
   ingress SG rules, no IGW route in the private route table.

---

## 11. Summary — design decisions and their rationale

| Decision                              | Why I made it |
|---------------------------------------|---------------|
| Two-tier (public/private) VPC         | Keep all workloads off the internet; public tier is just for NAT egress. |
| `10.42.0.0/16` CIDR                   | Uncommon range → safe to peer later without overlap. |
| `/24` subnets via `cidrsubnet`        | Self-documenting (`.0.x` public, `.100.x` private), computed not hand-typed. |
| 2 AZs                                 | AZ fault tolerance + RDS's 2-AZ subnet-group requirement. |
| **Single** NAT Gateway               | POC cost saving; production would use one NAT per AZ. |
| **S3 Gateway endpoint**               | Free + faster + private; keeps high-volume S3 traffic off the (paid) NAT. |
| SGs reference each other by **SG ID** | Identity-based microsegmentation; survives IP churn; DB reachable by exactly one workload. |
| `sg-fargate` no inbound               | Batch worker takes no connections — strongest posture. |
| RDS `publicly_accessible = false`     | Databases get no public endpoint; reachable only from inside the VPC. |
| Fargate `AssignPublicIp = DISABLED`   | Forces all egress through the NAT; no workload is internet-addressable. |

**One-line summary:** a dedicated 2-AZ VPC where every workload lives in a private subnet,
reaches AWS services outbound-only through a single NAT (with S3 traffic short-circuited over a
free gateway endpoint), and is firewalled by identity-based security groups so the databases
are reachable by exactly one thing — the Fargate task — and nothing from the internet can ever
initiate a connection inward.
