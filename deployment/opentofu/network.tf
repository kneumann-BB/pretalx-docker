# Networking. When var.vpc_id is null, a VPC with public and private subnets
# across var.vpc_az_count AZs is created; otherwise the supplied VPC and
# subnets are used as-is.

locals {
  create_vpc = var.vpc_id == null

  azs       = local.create_vpc ? slice(data.aws_availability_zones.available[0].names, 0, var.vpc_az_count) : []
  nat_count = local.create_vpc && var.nat_gateway_enabled ? (var.single_nat_gateway ? 1 : var.vpc_az_count) : 0

  vpc_id             = local.create_vpc ? aws_vpc.pretalx[0].id : var.vpc_id
  public_subnet_ids  = local.create_vpc ? aws_subnet.public[*].id : var.public_subnet_ids
  private_subnet_ids = local.create_vpc ? aws_subnet.private[*].id : var.private_subnet_ids

  # Without NAT, tasks need a public IP in a public subnet to reach ECR, SES, etc.
  tasks_public    = local.create_vpc && !var.nat_gateway_enabled
  task_subnet_ids = local.tasks_public ? local.public_subnet_ids : local.private_subnet_ids
  task_public_ip  = local.tasks_public
}

data "aws_availability_zones" "available" {
  count = local.create_vpc ? 1 : 0

  state = "available"
}

resource "aws_vpc" "pretalx" {
  count = local.create_vpc ? 1 : 0

  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = local.identifier
  }
}

resource "aws_internet_gateway" "pretalx" {
  count = local.create_vpc ? 1 : 0

  vpc_id = aws_vpc.pretalx[0].id

  tags = {
    Name = local.identifier
  }
}

# /20 subnets: public use netnums 0..n-1, private use 8..8+n-1.
resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id                  = aws_vpc.pretalx[0].id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.identifier}-public-${local.azs[count.index]}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  count = length(local.azs)

  vpc_id            = aws_vpc.pretalx[0].id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 8)

  tags = {
    Name = "${local.identifier}-private-${local.azs[count.index]}"
    Tier = "private"
  }
}

resource "aws_route_table" "public" {
  count = local.create_vpc ? 1 : 0

  vpc_id = aws_vpc.pretalx[0].id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.pretalx[0].id
  }

  tags = {
    Name = "${local.identifier}-public"
  }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public[0].id
}

resource "aws_eip" "nat" {
  count = local.nat_count

  domain = "vpc"

  tags = {
    Name = "${local.identifier}-nat-${local.azs[count.index]}"
  }

  depends_on = [aws_internet_gateway.pretalx]
}

resource "aws_nat_gateway" "pretalx" {
  count = local.nat_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "${local.identifier}-${local.azs[count.index]}"
  }
}

resource "aws_route_table" "private" {
  count = length(aws_subnet.private)

  vpc_id = aws_vpc.pretalx[0].id

  dynamic "route" {
    for_each = var.nat_gateway_enabled ? [1] : []

    content {
      cidr_block     = "0.0.0.0/0"
      nat_gateway_id = aws_nat_gateway.pretalx[var.single_nat_gateway ? 0 : count.index].id
    }
  }

  tags = {
    Name = "${local.identifier}-private-${local.azs[count.index]}"
  }
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# Free gateway endpoint so ECR image layer pulls (served from S3) skip the NAT.
resource "aws_vpc_endpoint" "s3" {
  count = local.create_vpc ? 1 : 0

  vpc_id            = aws_vpc.pretalx[0].id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(aws_route_table.public[*].id, aws_route_table.private[*].id)

  tags = {
    Name = "${local.identifier}-s3"
  }
}
