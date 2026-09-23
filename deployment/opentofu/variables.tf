variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short project name used for resource naming."
  type        = string
  default     = "pretalx"
}

variable "domain_name" {
  description = "Public DNS name for the pretalx instance, for example pretalx.example.com."
  type        = string
}

variable "route53_zone_id" {
  description = "Route53 hosted zone ID that contains domain_name."
  type        = string
}

variable "vpc_id" {
  description = "Existing VPC ID to deploy into. If null, OpenTofu creates a new VPC with public and private subnets and NAT."
  type        = string
  default     = null
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for the internet-facing ALB. Required when vpc_id is set; ignored otherwise."
  type        = list(string)
  default     = []

  validation {
    condition     = var.vpc_id == null || length(var.public_subnet_ids) >= 2
    error_message = "public_subnet_ids must list at least two subnets when vpc_id is set."
  }
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for ECS tasks, EFS, RDS, and ElastiCache. These subnets should have NAT or equivalent egress. Required when vpc_id is set; ignored otherwise."
  type        = list(string)
  default     = []

  validation {
    condition     = var.vpc_id == null || length(var.private_subnet_ids) >= 2
    error_message = "private_subnet_ids must list at least two subnets when vpc_id is set."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the created VPC. Only used when vpc_id is null."
  type        = string
  default     = "10.40.0.0/16"
}

variable "vpc_az_count" {
  description = "Number of availability zones to spread created subnets across. Only used when vpc_id is null."
  type        = number
  default     = 2

  validation {
    condition     = var.vpc_az_count >= 2
    error_message = "vpc_az_count must be at least 2 (ALB and RDS require two AZs)."
  }
}

variable "single_nat_gateway" {
  description = "Use one shared NAT gateway instead of one per AZ. Cheaper, but private egress depends on a single AZ. Only used when vpc_id is null."
  type        = bool
  default     = true
}

variable "image_tag" {
  description = "Container image tag to deploy from the Terraform-managed ECR repository."
  type        = string
}

variable "container_cpu_architecture" {
  description = "CPU architecture for ECS tasks. Valid values are X86_64 and ARM64."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.container_cpu_architecture)
    error_message = "container_cpu_architecture must be X86_64 or ARM64."
  }
}

variable "smtp_from" {
  description = "From address used by pretalx mail settings."
  type        = string
}

variable "smtp_host" {
  description = "SMTP host for pretalx mail delivery."
  type        = string
}

variable "smtp_port" {
  description = "SMTP port for pretalx mail delivery."
  type        = number
  default     = 587
}

variable "smtp_user" {
  description = "SMTP username for pretalx mail delivery."
  type        = string
}

variable "smtp_password" {
  description = "SMTP password for pretalx mail delivery."
  type        = string
  sensitive   = true
}

variable "smtp_tls" {
  description = "Whether pretalx should use STARTTLS for SMTP."
  type        = bool
  default     = true
}

variable "postgres_db_name" {
  description = "PostgreSQL database name for pretalx."
  type        = string
  default     = "pretalx"
}

variable "postgres_username" {
  description = "PostgreSQL master username for pretalx."
  type        = string
  default     = "pretalx"
}

variable "postgres_password" {
  description = "Optional explicit PostgreSQL password. If null, Terraform generates one."
  type        = string
  default     = null
  sensitive   = true
}

variable "postgres_instance_class" {
  description = "RDS instance class for PostgreSQL."
  type        = string
  default     = "db.t4g.medium"
}

variable "postgres_allocated_storage" {
  description = "Allocated storage in GiB for PostgreSQL."
  type        = number
  default     = 30
}

variable "postgres_engine_version" {
  description = "PostgreSQL engine major or minor version."
  type        = string
  default     = "15"
}

variable "postgres_backup_retention_period" {
  description = "Backup retention period for the RDS instance in days."
  type        = number
  default     = 7
}

variable "skip_final_snapshot" {
  description = "Whether to skip the final RDS snapshot when destroying the stack."
  type        = bool
  default     = true
}

variable "redis_node_type" {
  description = "ElastiCache node type for Redis."
  type        = string
  default     = "cache.t4g.small"
}

variable "redis_engine_version" {
  description = "Redis engine version for ElastiCache."
  type        = string
  default     = "7.1"
}

variable "redis_num_cache_clusters" {
  description = "Number of Redis cache clusters in the replication group."
  type        = number
  default     = 1
}

variable "redis_auth_token" {
  description = "Optional explicit Redis auth token. If null, Terraform generates one."
  type        = string
  default     = null
  sensitive   = true
}

variable "web_task_cpu" {
  description = "CPU units for the web task definition."
  type        = number
  default     = 1024
}

variable "web_task_memory" {
  description = "Memory in MiB for the web task definition."
  type        = number
  default     = 2048
}

variable "worker_task_cpu" {
  description = "CPU units for the worker task definition."
  type        = number
  default     = 1024
}

variable "worker_task_memory" {
  description = "Memory in MiB for the worker task definition."
  type        = number
  default     = 2048
}

variable "cron_task_cpu" {
  description = "CPU units for the cron task definition."
  type        = number
  default     = 512
}

variable "cron_task_memory" {
  description = "Memory in MiB for the cron task definition."
  type        = number
  default     = 1024
}

variable "web_desired_count" {
  description = "Desired task count for the web ECS service."
  type        = number
  default     = 1
}

variable "worker_desired_count" {
  description = "Desired task count for the worker ECS service."
  type        = number
  default     = 1
}

variable "web_min_capacity" {
  description = "Minimum autoscaling capacity for the web ECS service."
  type        = number
  default     = 1
}

variable "web_max_capacity" {
  description = "Maximum autoscaling capacity for the web ECS service."
  type        = number
  default     = 3
}

variable "web_cpu_target" {
  description = "Target average CPU utilization for web service autoscaling."
  type        = number
  default     = 60
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days."
  type        = number
  default     = 30
}

variable "nginx_image" {
  description = "Nginx image used by the web sidecar."
  type        = string
  default     = "public.ecr.aws/nginx/nginx:1.27-alpine"
}

variable "extra_pretalx_config" {
  description = "Additional lines appended to pretalx.cfg."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags applied to resources."
  type        = map(string)
  default     = {}
}