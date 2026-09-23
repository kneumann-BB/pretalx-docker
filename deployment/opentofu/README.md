# OpenTofu deployment for pretalx on ECS Fargate

This OpenTofu stack deploys pretalx on standard AWS ECS Fargate.

It provisions:

- a VPC with public/private subnets, NAT, and an S3 gateway endpoint (unless an existing `vpc_id` is supplied)
- an ECR repository for the pretalx image
- an ECS cluster
- an internet-facing ALB with ACM and Route53 DNS
- an ECS web service with an nginx sidecar
- an ECS worker service for Celery
- an EventBridge Scheduler job for `pretalx cron`
- an RDS PostgreSQL instance
- an ElastiCache Redis replication group
- an EFS file system shared across web, worker, and cron tasks
- a Secrets Manager secret containing the rendered `pretalx.cfg`

## Prerequisites

- Optionally, an existing VPC with at least two public subnets (ALB) and two private subnets (ECS, RDS, ElastiCache, EFS). If `vpc_id` is left unset, the stack creates one.
- Route53 hosting the target domain
- When bringing your own VPC: outbound internet access from the private subnets, typically through NAT, so Fargate can pull images and reach SMTP
- Docker installed locally to build and push the image

## Files

- `backend.tf` declares a partially-configured S3 backend
- `backend.hcl.example` shows the backend values to supply at init time
- `versions.tf` configures providers
- `variables.tf` defines inputs
- `network.tf` creates the VPC, subnets, and NAT when `vpc_id` is unset
- `main.tf` provisions the infrastructure and ECS services
- `pretalx.cfg.tftpl` renders the pretalx config written to EFS at runtime
- `pretalx.auto.tfvars.example` shows a minimal variable set
- `bootstrap/` is a one-time, separate stack that creates the S3 state bucket

## Remote state (S3 backend)

State is stored in S3 with S3-native locking (`use_lockfile`, via a `.tflock` object next to the state) instead of local `.tfstate` files. The bucket must exist before you can initialize this stack, and it is managed by a separate `bootstrap/` configuration so the state backend isn't stored inside the state it manages.

1. Create the backend resources once per AWS account/environment:

   ```bash
   cd bootstrap
   tofu init
   tofu apply \
     -var="aws_region=us-east-1" \
     -var="state_bucket_name=your-unique-pretalx-state-bucket"
   cd ..
   ```

2. Copy `backend.hcl.example` to `backend.hcl` and fill in the bucket name and region from the bootstrap outputs. Do not commit `backend.hcl`; it is already gitignored.

## Usage

1. Copy `pretalx.auto.tfvars.example` to `pretalx.auto.tfvars` and fill in real values. OpenTofu loads `*.auto.tfvars` automatically; the copy is gitignored.
2. Initialize OpenTofu with the S3 backend config:

   ```bash
   tofu init -backend-config=backend.hcl
   ```

3. Review the plan:

   ```bash
   tofu plan
   ```

4. Apply the infrastructure:

   ```bash
   tofu apply
   ```

5. Build and push the image to the ECR repository URL shown in the outputs.

6. Re-run OpenTofu if you changed `image_tag`, or force a new deployment for the ECS services after pushing the tag.

## Bootstrap behavior

The ECS task definitions include a short bootstrap container that:

- creates the pretalx directory structure on EFS
- writes the rendered `pretalx.cfg` from Secrets Manager to `/etc/pretalx/pretalx.cfg`

That removes the need for a separate EC2 host or a manual EFS mount just to place the config file.

## First-time initialization

The web service automatically runs migrations and static rebuilds on startup.

To create the first pretalx user, use ECS Exec against a running web task and run:

```bash
pretalx init
```

## Notes

- The ALB serves HTTPS and forwards to the nginx sidecar on port `80`.
- The nginx sidecar serves `/static/` and `/media/` directly from the shared EFS volume.
- The worker and cron tasks share the same EFS, PostgreSQL, and Redis backends as the web service.
- The OpenTofu state contains secrets (database and Redis credentials). The S3 backend bucket has versioning, default encryption, and blocked public access, but you should also restrict bucket access with IAM to only the principals that run OpenTofu.