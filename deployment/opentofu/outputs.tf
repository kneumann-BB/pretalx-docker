output "application_url" {
  description = "Public URL for the pretalx deployment."
  value       = local.site_url
}

output "ecr_repository_url" {
  description = "ECR repository URL to push the pretalx image to."
  value       = aws_ecr_repository.pretalx.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name used by web, worker, and cron tasks."
  value       = aws_ecs_cluster.pretalx.name
}

output "web_service_name" {
  description = "Web ECS service name."
  value       = aws_ecs_service.web.name
}

output "worker_service_name" {
  description = "Worker ECS service name."
  value       = aws_ecs_service.worker.name
}

output "cron_task_definition_arn" {
  description = "Cron ECS task definition ARN used by EventBridge Scheduler."
  value       = aws_ecs_task_definition.cron.arn
}

output "alb_dns_name" {
  description = "DNS name of the public ALB."
  value       = aws_lb.pretalx.dns_name
}

output "postgres_endpoint" {
  description = "RDS endpoint hostname."
  value       = aws_db_instance.postgres.address
}

output "redis_primary_endpoint" {
  description = "Primary Redis endpoint hostname."
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "pretalx_config_secret_arn" {
  description = "Secrets Manager ARN containing the rendered pretalx.cfg."
  value       = aws_secretsmanager_secret.pretalx_config.arn
}

output "efs_file_system_id" {
  description = "EFS file system ID shared across services."
  value       = aws_efs_file_system.pretalx.id
}
output "vpc_id" {
  description = "VPC ID the deployment runs in (created or supplied)."
  value       = local.vpc_id
}

output "public_subnet_ids" {
  description = "Public subnet IDs used by the ALB."
  value       = local.public_subnet_ids
}

output "private_subnet_ids" {
  description = "Private subnet IDs used by ECS, EFS, RDS, and ElastiCache."
  value       = local.private_subnet_ids
}
