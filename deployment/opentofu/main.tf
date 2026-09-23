data "aws_caller_identity" "current" {}

locals {
  identifier = substr(lower(replace(replace(replace(var.project_name, "_", "-"), " ", "-"), ".", "-")), 0, 32)
  tags = merge(
    {
      Project   = var.project_name
      ManagedBy = "opentofu"
    },
    var.tags,
  )

  db_password      = var.postgres_password != null ? var.postgres_password : random_password.postgres.result
  redis_auth_token = var.redis_auth_token != null ? var.redis_auth_token : random_password.redis.result
  app_image        = "${aws_ecr_repository.pretalx.repository_url}:${var.image_tag}"
  site_url         = "https://${var.domain_name}"

  bootstrap_script = <<-EOT
    set -euo pipefail
    mkdir -p /etc/pretalx /data/logs /public/media
    printf '%s' "$PRETALX_CONFIG" > /etc/pretalx/pretalx.cfg
  EOT

  nginx_config = <<-EOT
    server {
        listen 80;
        server_name _;

        location / {
            proxy_pass http://127.0.0.1:8346/;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header Host $http_host;
        }

        location /media/ {
            alias /public/media/;
            add_header Content-Disposition 'attachment; filename="$1"';
            expires 7d;
            access_log off;
        }
    }
  EOT

  pretalx_config = templatefile("${path.module}/pretalx.cfg.tftpl", {
    site_url         = local.site_url
    db_name          = var.postgres_db_name
    db_user          = var.postgres_username
    db_password      = local.db_password
    db_host          = aws_db_instance.postgres.address
    smtp_from        = var.smtp_from
    smtp_host        = var.smtp_host
    smtp_port        = var.smtp_port
    smtp_user        = var.smtp_user
    smtp_password    = var.smtp_password
    smtp_tls         = var.smtp_tls ? "True" : "False"
    redis_auth_token = local.redis_auth_token
    redis_host       = aws_elasticache_replication_group.redis.primary_endpoint_address
    extra_config     = trimspace(var.extra_pretalx_config)
  })

  common_mount_points = [
    {
      sourceVolume  = "pretalx-config"
      containerPath = "/etc/pretalx"
      readOnly      = false
    },
    {
      sourceVolume  = "pretalx-data"
      containerPath = "/data"
      readOnly      = false
    },
    {
      sourceVolume  = "pretalx-public"
      containerPath = "/public"
      readOnly      = false
    },
  ]

  bootstrap_container = {
    name       = "bootstrap"
    image      = local.app_image
    essential  = false
    entryPoint = ["/bin/bash", "-ec"]
    command    = [local.bootstrap_script]
    secrets = [
      {
        name      = "PRETALX_CONFIG"
        valueFrom = aws_secretsmanager_secret.pretalx_config.arn
      }
    ]
    mountPoints = local.common_mount_points
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.web_app.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "bootstrap"
      }
    }
  }
}

resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "aws_ecr_repository" "pretalx" {
  name                 = local.identifier
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecs_cluster" "pretalx" {
  name = local.identifier

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "web_app" {
  name              = "/ecs/${local.identifier}/web-app"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "web_nginx" {
  name              = "/ecs/${local.identifier}/web-nginx"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.identifier}/worker"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "cron" {
  name              = "/ecs/${local.identifier}/cron"
  retention_in_days = var.log_retention_days
}

resource "aws_security_group" "alb" {
  name_prefix = "${local.identifier}-alb-"
  description = "ALB security group for pretalx"
  vpc_id      = local.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "app" {
  name_prefix = "${local.identifier}-app-"
  description = "App security group for pretalx ECS tasks"
  vpc_id      = local.vpc_id

  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "postgres" {
  name_prefix = "${local.identifier}-postgres-"
  description = "PostgreSQL security group for pretalx"
  vpc_id      = local.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
}

resource "aws_security_group" "redis" {
  name_prefix = "${local.identifier}-redis-"
  description = "Redis security group for pretalx"
  vpc_id      = local.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
}

resource "aws_security_group" "efs" {
  name_prefix = "${local.identifier}-efs-"
  description = "EFS security group for pretalx"
  vpc_id      = local.vpc_id

  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
}

resource "aws_db_subnet_group" "postgres" {
  name       = local.identifier
  subnet_ids = local.private_subnet_ids
}

resource "aws_db_instance" "postgres" {
  identifier                 = local.identifier
  engine                     = "postgres"
  engine_version             = var.postgres_engine_version
  instance_class             = var.postgres_instance_class
  allocated_storage          = var.postgres_allocated_storage
  db_name                    = var.postgres_db_name
  username                   = var.postgres_username
  password                   = local.db_password
  storage_encrypted          = true
  backup_retention_period    = var.postgres_backup_retention_period
  db_subnet_group_name       = aws_db_subnet_group.postgres.name
  vpc_security_group_ids     = [aws_security_group.postgres.id]
  publicly_accessible        = false
  skip_final_snapshot        = var.skip_final_snapshot
  deletion_protection        = false
  auto_minor_version_upgrade = true
  apply_immediately          = true
}

resource "aws_elasticache_subnet_group" "redis" {
  name       = local.identifier
  subnet_ids = local.private_subnet_ids
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = local.identifier
  description                = "pretalx redis"
  engine                     = "redis"
  engine_version             = var.redis_engine_version
  node_type                  = var.redis_node_type
  num_cache_clusters         = var.redis_num_cache_clusters
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.redis.name
  security_group_ids         = [aws_security_group.redis.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = local.redis_auth_token
  automatic_failover_enabled = var.redis_num_cache_clusters > 1
  multi_az_enabled           = var.redis_num_cache_clusters > 1
  apply_immediately          = true
}

resource "aws_efs_file_system" "pretalx" {
  creation_token = local.identifier
  encrypted      = true

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }
}

resource "aws_efs_access_point" "config" {
  file_system_id = aws_efs_file_system.pretalx.id

  posix_user {
    uid = 999
    gid = 999
  }

  root_directory {
    path = "/pretalx/config"

    creation_info {
      owner_gid   = 999
      owner_uid   = 999
      permissions = "0775"
    }
  }
}

resource "aws_efs_access_point" "data" {
  file_system_id = aws_efs_file_system.pretalx.id

  posix_user {
    uid = 999
    gid = 999
  }

  root_directory {
    path = "/pretalx/data"

    creation_info {
      owner_gid   = 999
      owner_uid   = 999
      permissions = "0775"
    }
  }
}

resource "aws_efs_access_point" "public" {
  file_system_id = aws_efs_file_system.pretalx.id

  posix_user {
    uid = 999
    gid = 999
  }

  root_directory {
    path = "/pretalx/public"

    creation_info {
      owner_gid   = 999
      owner_uid   = 999
      permissions = "0775"
    }
  }
}

resource "aws_efs_mount_target" "pretalx" {
  for_each = { for i, id in local.private_subnet_ids : i => id }

  file_system_id  = aws_efs_file_system.pretalx.id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs.id]
}

resource "aws_secretsmanager_secret" "pretalx_config" {
  name                    = "${local.identifier}-pretalx-config"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "pretalx_config" {
  secret_id     = aws_secretsmanager_secret.pretalx_config.id
  secret_string = local.pretalx_config
}

data "aws_iam_policy_document" "ecs_task_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.identifier}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume_role.json
}

resource "aws_iam_role_policy_attachment" "execution_default" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secret_access" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.pretalx_config.arn]
  }
}

resource "aws_iam_role_policy" "execution_secret_access" {
  name   = "pretalx-config-secret"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secret_access.json
}

resource "aws_iam_role" "task" {
  name               = "${local.identifier}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume_role.json
}

data "aws_iam_policy_document" "task_exec" {
  statement {
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task_exec" {
  name   = "ecs-exec"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_exec.json
}

resource "aws_acm_certificate" "pretalx" {
  domain_name       = var.domain_name
  validation_method = "DNS"
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.pretalx.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id = var.route53_zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

resource "aws_acm_certificate_validation" "pretalx" {
  certificate_arn         = aws_acm_certificate.pretalx.arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

resource "aws_lb" "pretalx" {
  name               = substr(local.identifier, 0, 32)
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = local.public_subnet_ids
}

resource "aws_lb_target_group" "pretalx" {
  name        = substr(local.identifier, 0, 32)
  port        = 80
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  health_check {
    path                = "/orga/"
    matcher             = "200-399"
    healthy_threshold   = 2
    unhealthy_threshold = 5
    timeout             = 5
    interval            = 30
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.pretalx.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.pretalx.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.pretalx.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.pretalx.arn
  }
}

resource "aws_route53_record" "pretalx" {
  zone_id = var.route53_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_lb.pretalx.dns_name
    zone_id                = aws_lb.pretalx.zone_id
    evaluate_target_health = true
  }
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.identifier}-web"
  cpu                      = tostring(var.web_task_cpu)
  memory                   = tostring(var.web_task_memory)
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.container_cpu_architecture
  }

  volume {
    name = "pretalx-config"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.pretalx.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.config.id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "pretalx-data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.pretalx.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.data.id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "pretalx-public"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.pretalx.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.public.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    local.bootstrap_container,
    {
      name      = "pretalx-web"
      image     = local.app_image
      essential = true
      command   = ["webworker"]
      dependsOn = [{ containerName = "bootstrap", condition = "SUCCESS" }]
      environment = [
        { name = "PRETALX_FILESYSTEM_MEDIA", value = "/public/media" },
        { name = "PRETALX_FILESYSTEM_STATIC", value = "/pretalx/src/static.dist" },
        { name = "GUNICORN_BIND_ADDR", value = "0.0.0.0:8346" },
        { name = "GUNICORN_FORWARDED_ALLOW_IPS", value = "*" },
        { name = "AUTOMIGRATE", value = "yes" },
        { name = "AUTOREBUILD", value = "yes" },
      ]
      mountPoints = local.common_mount_points
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.web_app.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "pretalx"
        }
      }
    },
    {
      name       = "nginx"
      image      = var.nginx_image
      essential  = true
      entryPoint = ["/bin/sh", "-ec"]
      command = [
        "printf '%s' \"$NGINX_CONFIG\" > /etc/nginx/conf.d/default.conf && exec nginx -g 'daemon off;'"
      ]
      dependsOn = [{ containerName = "bootstrap", condition = "SUCCESS" }]
      environment = [
        { name = "NGINX_CONFIG", value = local.nginx_config },
      ]
      portMappings = [
        {
          containerPort = 80
          protocol      = "tcp"
        }
      ]
      mountPoints = [
        {
          sourceVolume  = "pretalx-public"
          containerPath = "/public"
          readOnly      = false
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.web_nginx.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "nginx"
        }
      }
    },
  ])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.identifier}-worker"
  cpu                      = tostring(var.worker_task_cpu)
  memory                   = tostring(var.worker_task_memory)
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.container_cpu_architecture
  }

  volume {
    name = "pretalx-config"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.pretalx.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.config.id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "pretalx-data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.pretalx.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.data.id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "pretalx-public"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.pretalx.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.public.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    local.bootstrap_container,
    {
      name      = "pretalx-worker"
      image     = local.app_image
      essential = true
      command   = ["taskworker"]
      dependsOn = [{ containerName = "bootstrap", condition = "SUCCESS" }]
      environment = [
        { name = "PRETALX_FILESYSTEM_MEDIA", value = "/public/media" },
        { name = "PRETALX_FILESYSTEM_STATIC", value = "/pretalx/src/static.dist" },
        { name = "AUTOMIGRATE", value = "no" },
        { name = "AUTOREBUILD", value = "no" },
      ]
      mountPoints = local.common_mount_points
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.worker.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "worker"
        }
      }
    },
  ])
}

resource "aws_ecs_task_definition" "cron" {
  family                   = "${local.identifier}-cron"
  cpu                      = tostring(var.cron_task_cpu)
  memory                   = tostring(var.cron_task_memory)
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.container_cpu_architecture
  }

  volume {
    name = "pretalx-config"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.pretalx.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.config.id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "pretalx-data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.pretalx.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.data.id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "pretalx-public"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.pretalx.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.public.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    local.bootstrap_container,
    {
      name      = "pretalx-cron"
      image     = local.app_image
      essential = true
      command   = ["cron"]
      dependsOn = [{ containerName = "bootstrap", condition = "SUCCESS" }]
      environment = [
        { name = "PRETALX_FILESYSTEM_MEDIA", value = "/public/media" },
        { name = "PRETALX_FILESYSTEM_STATIC", value = "/pretalx/src/static.dist" },
        { name = "AUTOMIGRATE", value = "no" },
        { name = "AUTOREBUILD", value = "no" },
      ]
      mountPoints = local.common_mount_points
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.cron.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "cron"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "web" {
  name                              = "${local.identifier}-web"
  cluster                           = aws_ecs_cluster.pretalx.id
  task_definition                   = aws_ecs_task_definition.web.arn
  desired_count                     = var.web_desired_count
  launch_type                       = "FARGATE"
  platform_version                  = "LATEST"
  enable_execute_command            = true
  health_check_grace_period_seconds = 300

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.pretalx.arn
    container_name   = "nginx"
    container_port   = 80
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "worker" {
  name                   = "${local.identifier}-worker"
  cluster                = aws_ecs_cluster.pretalx.id
  task_definition        = aws_ecs_task_definition.worker.arn
  desired_count          = var.worker_desired_count
  launch_type            = "FARGATE"
  platform_version       = "LATEST"
  enable_execute_command = true

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

resource "aws_appautoscaling_target" "web" {
  max_capacity       = var.web_max_capacity
  min_capacity       = var.web_min_capacity
  resource_id        = "service/${aws_ecs_cluster.pretalx.name}/${aws_ecs_service.web.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "web_cpu" {
  name               = "${local.identifier}-web-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.web.resource_id
  scalable_dimension = aws_appautoscaling_target.web.scalable_dimension
  service_namespace  = aws_appautoscaling_target.web.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }

    target_value = var.web_cpu_target
  }
}

data "aws_iam_policy_document" "scheduler_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.identifier}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume_role.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions = ["ecs:RunTask"]
    resources = [
      aws_ecs_task_definition.cron.arn,
      "${aws_ecs_task_definition.cron.arn_without_revision}:*",
    ]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.pretalx.arn]
    }
  }

  statement {
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.execution.arn,
      aws_iam_role.task.arn,
    ]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "run-cron-task"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_scheduler_schedule" "cron" {
  name                = "${local.identifier}-cron"
  group_name          = "default"
  schedule_expression = "cron(15,45 * * * ? *)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.pretalx.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.cron.arn
      launch_type         = "FARGATE"
      platform_version    = "LATEST"
      task_count          = 1

      network_configuration {
        subnets          = local.private_subnet_ids
        security_groups  = [aws_security_group.app.id]
        assign_public_ip = false
      }
    }
  }
}