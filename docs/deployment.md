# Error Triage → Jira Upserter - Deployment Guide

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [AWS Infrastructure Setup](#2-aws-infrastructure-setup)
3. [AWS Secrets Manager Configuration](#3-aws-secrets-manager-configuration)
4. [Docker Image Build and Push](#4-docker-image-build-and-push)
5. [Terraform Deployment](#5-terraform-deployment)
6. [First Deployment Checklist](#6-first-deployment-checklist)
7. [Blue-Green Rolling Deployment](#7-blue-green-rolling-deployment)
8. [Environment-Specific Configurations](#8-environment-specific-configurations)
9. [Post-Deployment Verification](#9-post-deployment-verification)
10. [Rollback Procedures](#10-rollback-procedures)
11. [CI/CD Pipeline Integration](#11-cicd-pipeline-integration)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Prerequisites

### 1.1 Required Tools and Access

Before deploying the Error Triage service, ensure you have the following tools installed and configured:

| Tool | Minimum Version | Purpose | Installation |
|------|----------------|---------|--------------|
| **AWS CLI** | 2.15+ | Interact with AWS services | `pip install awscli --upgrade` or https://aws.amazon.com/cli/ |
| **Docker** | 24.0+ | Build container images | https://docs.docker.com/get-docker/ |
| **Terraform** | 1.6.0+ | Infrastructure provisioning | https://www.terraform.io/downloads |
| **Git** | 2.40+ | Source code management | https://git-scm.com/downloads |
| **jq** | 1.6+ | JSON parsing for scripts | `apt-get install jq` or `brew install jq` |
| **curl** | 7.88+ | API testing and health checks | Pre-installed on most systems |

### 1.2 AWS Account Requirements

**AWS Account Setup:**

1. **AWS Account with Programmatic Access**
   - AWS account ID: `{your-aws-account-id}`
   - IAM user or role with appropriate permissions
   - AWS CLI configured with credentials: `aws configure`
   - Verify access: `aws sts get-caller-identity`

2. **Required IAM Permissions**

   Your IAM user or role must have the following permissions:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "ecs:*",
           "ec2:DescribeVpcs",
           "ec2:DescribeSubnets",
           "ec2:DescribeSecurityGroups",
           "ec2:CreateSecurityGroup",
           "ec2:AuthorizeSecurityGroupIngress",
           "elasticloadbalancing:*",
           "elasticache:*",
           "secretsmanager:*",
           "logs:*",
           "ecr:*",
           "iam:GetRole",
           "iam:PassRole",
           "iam:CreateRole",
           "iam:AttachRolePolicy",
           "s3:*",
           "dynamodb:*"
         ],
         "Resource": "*"
       }
     ]
   }
   ```

3. **AWS CLI Configuration**

   ```bash
   # Configure AWS CLI with your credentials
   aws configure
   # AWS Access Key ID: [Your Access Key]
   # AWS Secret Access Key: [Your Secret Key]
   # Default region name: us-east-1
   # Default output format: json

   # Verify configuration
   aws sts get-caller-identity
   # Expected output:
   # {
   #   "UserId": "AIDAI...",
   #   "Account": "123456789012",
   #   "Arn": "arn:aws:iam::123456789012:user/your-username"
   # }
   ```

### 1.3 Existing AWS Infrastructure

The following AWS resources must exist **before** deploying the Error Triage service:

#### VPC and Networking

| Resource | Name/ID | Configuration | Purpose |
|----------|---------|---------------|---------|
| **VPC** | `jiratest-prod-vpc` | CIDR: `10.0.0.0/16` | Primary network for all services |
| **Public Subnets** | `jiratest-public-{az}` | 3 subnets across AZs (us-east-1a, us-east-1b, us-east-1c) | Host Application Load Balancer |
| **Private Subnets** | `jiratest-private-{az}` | 3 subnets across AZs | Host ECS tasks and ElastiCache Redis |
| **Internet Gateway** | `jiratest-igw` | Attached to VPC | Public subnet internet access |
| **NAT Gateway** | `jiratest-nat-{az}` | One per public subnet | Private subnet outbound internet |
| **Route Tables** | Public and Private | Configured for IGW and NAT | Network traffic routing |

**Verify VPC Setup:**

```bash
# List VPCs
aws ec2 describe-vpcs --filters "Name=tag:Name,Values=jiratest-prod-vpc" --query 'Vpcs[0].VpcId' --output text

# List subnets
aws ec2 describe-subnets --filters "Name=vpc-id,Values=<vpc-id>" --query 'Subnets[*].[SubnetId,AvailabilityZone,Tags[?Key==`Name`].Value|[0]]' --output table
```

#### Application Load Balancer

| Resource | Name | Configuration | Purpose |
|----------|------|---------------|---------|
| **ALB** | `jiratest-alb` | Internet-facing, multi-AZ | HTTPS termination and traffic routing |
| **Target Group** | `jiratest-error-triage-tg` | Port 8080, health check: `/healthz` | ECS task registration |
| **HTTPS Listener** | Port 443 | TLS certificate from ACM | Secure webhook ingress |
| **ACM Certificate** | `error-triage.jiratest.com` | Validated for domain | TLS encryption |

**Verify ALB Setup:**

```bash
# List ALBs
aws elbv2 describe-load-balancers --names jiratest-alb --query 'LoadBalancers[0].[LoadBalancerArn,DNSName]' --output table

# Verify HTTPS listener
aws elbv2 describe-listeners --load-balancer-arn <alb-arn> --query 'Listeners[?Port==`443`]' --output table
```

#### Elastic Container Registry (ECR)

| Resource | Name | Configuration | Purpose |
|----------|------|---------------|---------|
| **ECR Repository** | `jiratest/error-triage` | Scan on push enabled | Docker image storage |

**Create ECR Repository (if not exists):**

```bash
# Create repository
aws ecr create-repository \
  --repository-name jiratest/error-triage \
  --image-scanning-configuration scanOnPush=true \
  --region us-east-1

# Configure lifecycle policy
cat > lifecycle-policy.json <<EOF
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Keep last 10 production releases",
      "selection": {
        "tagStatus": "tagged",
        "tagPrefixList": ["v"],
        "countType": "imageCountMoreThan",
        "countNumber": 10
      },
      "action": {
        "type": "expire"
      }
    },
    {
      "rulePriority": 2,
      "description": "Keep images from last 30 days",
      "selection": {
        "tagStatus": "any",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": 30
      },
      "action": {
        "type": "expire"
      }
    },
    {
      "rulePriority": 3,
      "description": "Delete untagged images after 7 days",
      "selection": {
        "tagStatus": "untagged",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": 7
      },
      "action": {
        "type": "expire"
      }
    }
  ]
}
EOF

aws ecr put-lifecycle-policy \
  --repository-name jiratest/error-triage \
  --lifecycle-policy-text file://lifecycle-policy.json
```

#### Terraform State Backend

| Resource | Name | Configuration | Purpose |
|----------|------|---------------|---------|
| **S3 Bucket** | `jiratest-terraform-state` | Versioning enabled, encryption enabled | Terraform state storage |
| **DynamoDB Table** | `jiratest-terraform-locks` | Hash key: `LockID` (String) | Terraform state locking |

**Create Terraform Backend Resources:**

```bash
# Create S3 bucket for Terraform state
aws s3api create-bucket \
  --bucket jiratest-terraform-state \
  --region us-east-1

# Enable versioning
aws s3api put-bucket-versioning \
  --bucket jiratest-terraform-state \
  --versioning-configuration Status=Enabled

# Enable encryption
aws s3api put-bucket-encryption \
  --bucket jiratest-terraform-state \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'

# Create DynamoDB table for state locking
aws dynamodb create-table \
  --table-name jiratest-terraform-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

---

## 2. AWS Infrastructure Setup

### 2.1 DNS Configuration

**Domain Setup:**

1. **Register or configure domain**: `error-triage.jiratest.com`
2. **Create Route 53 hosted zone** (if using Route 53)
3. **Point DNS to ALB**:

```bash
# Get ALB DNS name
ALB_DNS=$(aws elbv2 describe-load-balancers --names jiratest-alb --query 'LoadBalancers[0].DNSName' --output text)

# Create Route 53 A record (alias to ALB)
cat > route53-record.json <<EOF
{
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "error-triage.jiratest.com",
      "Type": "A",
      "AliasTarget": {
        "HostedZoneId": "Z35SXDOTRQ7X7K",
        "DNSName": "${ALB_DNS}",
        "EvaluateTargetHealth": true
      }
    }
  }]
}
EOF

aws route53 change-resource-record-sets \
  --hosted-zone-id <your-hosted-zone-id> \
  --change-batch file://route53-record.json
```

### 2.2 SSL/TLS Certificate

**ACM Certificate Setup:**

```bash
# Request certificate (if not already created)
aws acm request-certificate \
  --domain-name error-triage.jiratest.com \
  --validation-method DNS \
  --region us-east-1

# Note the CertificateArn from output

# Validate certificate (follow DNS validation records)
aws acm describe-certificate \
  --certificate-arn <certificate-arn> \
  --query 'Certificate.DomainValidationOptions'

# Add the CNAME records to your DNS provider
# Wait for validation (can take 5-30 minutes)
```

### 2.3 Security Groups

The Terraform configuration will create the following security groups, but you should understand the traffic rules:

**ECS Service Security Group:**
- **Inbound**: Port 8080 from ALB security group
- **Outbound**: 
  - Port 443 to `0.0.0.0/0` (Jira API, AWS Secrets Manager)
  - Port 6379 to Redis security group
  - Port 27017 to MongoDB Atlas CIDR (if enabled)

**Redis Security Group:**
- **Inbound**: Port 6379 from ECS security group
- **Outbound**: None required

**ALB Security Group:**
- **Inbound**: 
  - Port 443 from Vercel IP ranges
  - Port 443 from GCP Cloud Pub/Sub IP ranges
  - Port 443 from `0.0.0.0/0` (if open webhook endpoint)
- **Outbound**: Port 8080 to ECS security group

---

## 3. AWS Secrets Manager Configuration

### 3.1 Overview

All sensitive credentials are stored in AWS Secrets Manager and loaded by ECS tasks at runtime. **Never hardcode credentials in environment variables or configuration files.**

### 3.2 Required Secrets

The service requires three secrets (MongoDB secret is optional if ENABLE_MONGO=false):

| Secret Name | Environment Variable | Contains |
|-------------|---------------------|----------|
| `jira/jiratest/{env}/credentials` | `JIRA_CREDENTIALS_SECRET_ARN` | Jira API credentials |
| `jira/jiratest/{env}/webhook-secret` | `WEBHOOK_SECRET_ARN` | Webhook authentication secrets |
| `mongodb/jiratest/{env}/connection-string` | `MONGODB_SECRET_ARN` | MongoDB Atlas connection string (optional) |

Replace `{env}` with: `dev`, `staging`, or `production`

### 3.3 Create Jira Credentials Secret

**Step 1: Prepare Jira API Token**

1. Log in to Atlassian (Jira Cloud)
2. Navigate to: https://id.atlassian.com/manage-profile/security/api-tokens
3. Click "Create API token"
4. Name: `error-triage-service`
5. Copy the generated token

**Step 2: Create Secret in AWS Secrets Manager**

```bash
# Set environment (dev/staging/production)
ENV="staging"

# Create secret with Jira credentials
aws secretsmanager create-secret \
  --name "jira/jiratest/${ENV}/credentials" \
  --description "Jira API credentials for Error Triage service - ${ENV} environment" \
  --secret-string '{
    "base_url": "https://your-organization.atlassian.net",
    "api_token": "ATATT3xFfGF0...",
    "email": "api-user@yourcompany.com",
    "project_key": "ET"
  }' \
  --region us-east-1

# Expected output:
# {
#   "ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:jira/jiratest/staging/credentials-AbCdEf",
#   "Name": "jira/jiratest/staging/credentials",
#   "VersionId": "..."
# }

# Note the ARN for Terraform configuration
```

**Secret JSON Structure:**

```json
{
  "base_url": "https://your-organization.atlassian.net",
  "api_token": "ATATT3xFfGF0...",
  "email": "api-user@yourcompany.com",
  "project_key": "ET"
}
```

| Field | Description | Example |
|-------|-------------|---------|
| `base_url` | Jira Cloud instance URL | `https://acme.atlassian.net` |
| `api_token` | Atlassian API token | `ATATT3xFfGF0...` |
| `email` | Email of API token user | `bot@acme.com` |
| `project_key` | Jira project key for error tracking | `ET` (Error Triage) |

### 3.4 Create Webhook Secret

**Step 1: Generate Webhook Secrets**

```bash
# Generate Vercel webhook secret (random string)
VERCEL_SECRET=$(openssl rand -hex 32)
echo "Vercel Webhook Secret: ${VERCEL_SECRET}"

# GCP Pub/Sub uses OIDC, so we just need the audience URL
GCP_AUDIENCE="https://error-triage.jiratest.com/events"
```

**Step 2: Create Secret in AWS Secrets Manager**

```bash
ENV="staging"

aws secretsmanager create-secret \
  --name "jira/jiratest/${ENV}/webhook-secret" \
  --description "Webhook authentication secrets for Error Triage service - ${ENV} environment" \
  --secret-string "{
    \"vercel\": \"${VERCEL_SECRET}\",
    \"gcp_audience\": \"${GCP_AUDIENCE}\"
  }" \
  --region us-east-1
```

**Secret JSON Structure:**

```json
{
  "vercel": "a1b2c3d4e5f6...",
  "gcp_audience": "https://error-triage.jiratest.com/events"
}
```

| Field | Description | Usage |
|-------|-------------|-------|
| `vercel` | HMAC secret for Vercel signature verification | Verify `x-vercel-signature` header |
| `gcp_audience` | OIDC audience for GCP JWT validation | Verify GCP Pub/Sub push authorization |

### 3.5 Create MongoDB Connection String Secret (Optional)

**Only required if `ENABLE_MONGO=true` for audit trail logging.**

```bash
ENV="staging"

# MongoDB Atlas connection string format:
# mongodb+srv://<username>:<password>@<cluster>.mongodb.net/<database>?retryWrites=true&w=majority

aws secretsmanager create-secret \
  --name "mongodb/jiratest/${ENV}/connection-string" \
  --description "MongoDB Atlas connection string for Error Triage service - ${ENV} environment" \
  --secret-string '{
    "uri": "mongodb+srv://error-triage:password@cluster0.mongodb.net/jiratest-staging?retryWrites=true&w=majority"
  }' \
  --region us-east-1
```

**Secret JSON Structure:**

```json
{
  "uri": "mongodb+srv://user:password@cluster.mongodb.net/jiratest-staging?retryWrites=true&w=majority"
}
```

### 3.6 IAM Policy for ECS Task Role

The ECS task role must have permission to retrieve these secrets:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:jira/jiratest/staging/credentials-*",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:jira/jiratest/staging/webhook-secret-*",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:mongodb/jiratest/staging/connection-string-*"
      ]
    }
  ]
}
```

**This policy is automatically created by the Terraform IAM module.**

### 3.7 Validate Secrets

```bash
ENV="staging"

# Test retrieval of Jira credentials
aws secretsmanager get-secret-value \
  --secret-id "jira/jiratest/${ENV}/credentials" \
  --query 'SecretString' \
  --output text | jq

# Expected output (values masked):
# {
#   "base_url": "https://acme.atlassian.net",
#   "api_token": "ATATT3xFfGF0...",
#   "email": "bot@acme.com",
#   "project_key": "ET"
# }

# Test webhook secret
aws secretsmanager get-secret-value \
  --secret-id "jira/jiratest/${ENV}/webhook-secret" \
  --query 'SecretString' \
  --output text | jq

# Test MongoDB secret (if enabled)
aws secretsmanager get-secret-value \
  --secret-id "mongodb/jiratest/${ENV}/connection-string" \
  --query 'SecretString' \
  --output text | jq
```

---

## 4. Docker Image Build and Push

### 4.1 Build Docker Image Locally

**Prerequisites:**
- Docker installed and running
- AWS CLI configured
- ECR repository created

**Build Process:**

```bash
# Clone repository
git clone https://github.com/your-org/jiratest-error-triage.git
cd jiratest-error-triage

# Build Docker image
docker build -t jiratest/error-triage:latest .

# Verify image
docker images | grep error-triage
# Expected output:
# jiratest/error-triage   latest   abc123def456   2 minutes ago   180MB

# Test image locally (optional)
docker run --rm -p 8080:8080 \
  -e REDIS_HOST=localhost \
  -e JIRA_BASE_URL=https://test.atlassian.net \
  -e LOG_LEVEL=DEBUG \
  jiratest/error-triage:latest
```

### 4.2 Push Image to Amazon ECR

```bash
# Set variables
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION="us-east-1"
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ECR_REPOSITORY="jiratest/error-triage"
IMAGE_TAG=$(git rev-parse --short HEAD)  # Use Git commit SHA as tag

# Authenticate Docker to ECR
aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin ${ECR_REGISTRY}

# Tag image for ECR
docker tag jiratest/error-triage:latest \
  ${ECR_REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}

docker tag jiratest/error-triage:latest \
  ${ECR_REGISTRY}/${ECR_REPOSITORY}:latest

# Push image to ECR
docker push ${ECR_REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}
docker push ${ECR_REGISTRY}/${ECR_REPOSITORY}:latest

# Verify push
aws ecr describe-images \
  --repository-name ${ECR_REPOSITORY} \
  --query 'imageDetails[*].[imageTags[0],imagePushedAt]' \
  --output table
```

### 4.3 Multi-Tag Strategy for Releases

For production deployments, apply multiple tags:

```bash
# Example: Deploying version v1.2.3 to production
VERSION="v1.2.3"
ENV="production"
GIT_SHA=$(git rev-parse --short HEAD)

# Tag with all identifiers
docker tag jiratest/error-triage:latest ${ECR_REGISTRY}/${ECR_REPOSITORY}:${GIT_SHA}
docker tag jiratest/error-triage:latest ${ECR_REGISTRY}/${ECR_REPOSITORY}:${VERSION}
docker tag jiratest/error-triage:latest ${ECR_REGISTRY}/${ECR_REPOSITORY}:${ENV}-latest
docker tag jiratest/error-triage:latest ${ECR_REGISTRY}/${ECR_REPOSITORY}:main

# Push all tags
docker push ${ECR_REGISTRY}/${ECR_REPOSITORY}:${GIT_SHA}
docker push ${ECR_REGISTRY}/${ECR_REPOSITORY}:${VERSION}
docker push ${ECR_REGISTRY}/${ECR_REPOSITORY}:${ENV}-latest
docker push ${ECR_REGISTRY}/${ECR_REPOSITORY}:main
```

---

## 5. Terraform Deployment

### 5.1 Initialize Terraform

```bash
# Navigate to Terraform directory
cd deploy/terraform

# Initialize Terraform with S3 backend
terraform init \
  -backend-config="bucket=jiratest-terraform-state" \
  -backend-config="key=error-triage/${ENV}/terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="dynamodb_table=jiratest-terraform-locks" \
  -backend-config="encrypt=true"

# Expected output:
# Initializing modules...
# Initializing the backend...
# Successfully configured the backend "s3"!
# Terraform has been successfully initialized!
```

### 5.2 Terraform Workspaces

Use Terraform workspaces to isolate environments:

```bash
# Create workspace for environment
terraform workspace new staging

# Or select existing workspace
terraform workspace select staging

# List workspaces
terraform workspace list
#   default
# * staging
#   production

# Show current workspace
terraform workspace show
# staging
```

### 5.3 Configure Environment Variables

Create an environment-specific variables file:

**`environments/staging.tfvars`:**

```hcl
# Environment configuration
environment = "staging"
aws_region  = "us-east-1"

# VPC configuration
vpc_id             = "vpc-0123456789abcdef0"
private_subnet_ids = ["subnet-abc123", "subnet-def456", "subnet-ghi789"]
public_subnet_ids  = ["subnet-xyz123", "subnet-uvw456", "subnet-rst789"]

# ECS configuration
ecs_cluster_name       = "jiratest-error-triage-staging-cluster"
ecs_service_name       = "jiratest-error-triage-staging"
ecs_task_cpu           = "512"   # 0.5 vCPU
ecs_task_memory        = "1024"  # 1 GB
ecs_desired_count      = 2
ecs_max_count          = 4
ecs_min_count          = 1

# Application Load Balancer
alb_arn                = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/jiratest-alb/..."
alb_listener_arn       = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/jiratest-alb/..."
alb_security_group_id  = "sg-0123456789abcdef0"

# ElastiCache Redis configuration
redis_node_type        = "cache.t4g.small"
redis_num_cache_nodes  = 2
redis_parameter_group  = "default.redis7"
redis_engine_version   = "7.2"

# Docker image
ecr_repository_url     = "123456789012.dkr.ecr.us-east-1.amazonaws.com/jiratest/error-triage"
docker_image_tag       = "staging-latest"

# Secrets Manager ARNs
jira_credentials_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:jira/jiratest/staging/credentials-AbCdEf"
webhook_secret_arn          = "arn:aws:secretsmanager:us-east-1:123456789012:secret:jira/jiratest/staging/webhook-secret-GhIjKl"
mongodb_secret_arn          = "arn:aws:secretsmanager:us-east-1:123456789012:secret:mongodb/jiratest/staging/connection-string-MnOpQr"

# Application configuration
enable_mongodb         = true
log_level              = "INFO"
```

### 5.4 Terraform Plan

Generate and review the execution plan:

```bash
# Generate plan for staging environment
terraform plan \
  -var-file=environments/staging.tfvars \
  -out=tfplan

# Review output:
# - ECS service and task definition
# - ElastiCache Redis cluster
# - Security groups
# - IAM roles and policies
# - CloudWatch log group

# Expected resources to create:
# Plan: 15 to add, 0 to change, 0 to destroy.
```

**Review Checklist:**

- [ ] ECS service created with correct task count
- [ ] Task definition references correct Docker image tag
- [ ] Security groups allow required traffic
- [ ] IAM task role has Secrets Manager permissions
- [ ] ElastiCache Redis cluster in correct subnets
- [ ] CloudWatch log group created
- [ ] Auto-scaling policies configured

### 5.5 Terraform Apply

Execute the plan to create infrastructure:

```bash
# Apply the plan
terraform apply tfplan

# Terraform will display planned changes and prompt for confirmation
# Type 'yes' to proceed

# Expected output:
# Apply complete! Resources: 15 added, 0 changed, 0 destroyed.
# 
# Outputs:
# ecs_cluster_name = "jiratest-error-triage-staging-cluster"
# ecs_service_name = "jiratest-error-triage-staging"
# redis_endpoint = "jiratest-error-triage-staging-redis.abc123.ng.0001.use1.cache.amazonaws.com:6379"
# service_url = "https://error-triage.jiratest.com"
# cloudwatch_log_group = "/aws/ecs/jiratest-error-triage-staging"
```

### 5.6 Wait for Service Stabilization

ECS service deployment typically takes 5-10 minutes:

```bash
# Wait for service to reach stable state
aws ecs wait services-stable \
  --cluster jiratest-error-triage-staging-cluster \
  --services jiratest-error-triage-staging

# Check service status
aws ecs describe-services \
  --cluster jiratest-error-triage-staging-cluster \
  --services jiratest-error-triage-staging \
  --query 'services[0].[status,runningCount,desiredCount]' \
  --output table

# Expected output:
# -----------------
# |  DescribeServices  |
# +--------+-------+-----+
# | ACTIVE |  2    |  2  |
# +--------+-------+-----+
```

---

## 6. First Deployment Checklist

Use this checklist for initial deployment to a new environment:

### 6.1 Pre-Deployment

- [ ] **AWS Prerequisites Verified**
  - [ ] VPC and subnets exist
  - [ ] ALB configured with HTTPS listener
  - [ ] ECR repository created
  - [ ] S3 backend and DynamoDB table for Terraform state
  - [ ] IAM permissions configured

- [ ] **Secrets Created in AWS Secrets Manager**
  - [ ] `jira/jiratest/{env}/credentials`
  - [ ] `jira/jiratest/{env}/webhook-secret`
  - [ ] `mongodb/jiratest/{env}/connection-string` (if ENABLE_MONGO=true)
  - [ ] Secrets validated with `aws secretsmanager get-secret-value`

- [ ] **Configuration Files Prepared**
  - [ ] `config/severity_rules.yaml` reviewed
  - [ ] `config/ownership_rules.yaml` customized
  - [ ] `config/sanitization_patterns.yaml` validated

### 6.2 Build and Push

- [ ] **Docker Image Built**
  ```bash
  docker build -t jiratest/error-triage:latest .
  ```

- [ ] **Image Tested Locally** (optional but recommended)
  ```bash
  docker run --rm -p 8080:8080 \
    -e REDIS_HOST=localhost \
    -e JIRA_BASE_URL=https://test.atlassian.net \
    jiratest/error-triage:latest
  ```

- [ ] **Image Pushed to ECR**
  ```bash
  docker push ${ECR_REGISTRY}/jiratest/error-triage:${IMAGE_TAG}
  ```

- [ ] **Image Tags Verified**
  ```bash
  aws ecr describe-images --repository-name jiratest/error-triage
  ```

### 6.3 Terraform Deployment

- [ ] **Terraform Initialized**
  ```bash
  cd deploy/terraform
  terraform init
  ```

- [ ] **Workspace Created/Selected**
  ```bash
  terraform workspace select staging
  ```

- [ ] **Plan Generated and Reviewed**
  ```bash
  terraform plan -var-file=environments/staging.tfvars -out=tfplan
  ```

- [ ] **Plan Approved by Team**
  - Peer review of Terraform plan output
  - Verify resource counts and configurations

- [ ] **Terraform Applied**
  ```bash
  terraform apply tfplan
  ```

- [ ] **Service Stabilized**
  ```bash
  aws ecs wait services-stable \
    --cluster jiratest-error-triage-staging-cluster \
    --services jiratest-error-triage-staging
  ```

### 6.4 Post-Deployment Verification

- [ ] **Health Check Passing**
  ```bash
  curl -f https://error-triage.jiratest.com/healthz
  # Expected: HTTP 200 with JSON response
  ```

- [ ] **Metrics Endpoint Accessible**
  ```bash
  curl https://error-triage.jiratest.com/metrics | grep events_received_total
  ```

- [ ] **CloudWatch Logs Visible**
  ```bash
  aws logs tail /aws/ecs/jiratest-error-triage-staging --follow
  ```

- [ ] **Test Webhook Submitted**
  ```bash
  # See section 9.4 for test webhook examples
  ```

- [ ] **Jira Issue Created**
  - Verify test webhook created Jira issue
  - Check issue labels, priority, description format

- [ ] **Redis Connectivity Verified**
  - Check `/healthz` response includes `"redis": {"status": "healthy"}`

- [ ] **MongoDB Connectivity Verified** (if enabled)
  - Check `/healthz` response includes `"mongodb": {"status": "healthy"}`

### 6.5 Monitoring Setup

- [ ] **CloudWatch Dashboard Created**
  - See `docs/monitoring.md` for dashboard configuration

- [ ] **CloudWatch Alarms Configured**
  - High error rate alarm
  - Service unavailable alarm
  - Redis connection failure alarm

- [ ] **Slack Notifications Configured** (optional)
  - SNS topic for alarms
  - Slack webhook integration

---

## 7. Blue-Green Rolling Deployment

### 7.1 Overview

The Error Triage service uses **ECS Rolling Update** strategy for zero-downtime deployments:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| **Minimum Healthy Percent** | 100% | Ensures full capacity during deployment |
| **Maximum Percent** | 200% | Allows doubling capacity temporarily |
| **Health Check Grace Period** | 60 seconds | Time for tasks to become healthy |
| **Connection Draining** | 30 seconds | Gracefully drain in-flight requests |
| **Deployment Circuit Breaker** | Enabled | Automatic rollback on failure |

### 7.2 Deployment Process

**Step-by-Step Rolling Update:**

1. **New Task Launch**
   - ECS launches 2 new tasks with updated task definition (Maximum 200%)
   - New tasks start in "PENDING" state
   - Load configuration from Secrets Manager
   - Connect to Redis and MongoDB (if enabled)

2. **Health Check Phase**
   - ALB performs health checks on new tasks: `GET /healthz`
   - Health check interval: 30 seconds
   - Required consecutive successes: 2
   - Total health check duration: ~60 seconds

3. **Traffic Shifting**
   - After health checks pass, ALB begins sending traffic to new tasks
   - Old tasks continue serving existing requests
   - No dropped connections

4. **Connection Draining**
   - Old tasks removed from ALB target group
   - ALB waits 30 seconds for in-flight requests to complete
   - New requests routed only to new tasks

5. **Old Task Termination**
   - After draining period, old tasks receive SIGTERM
   - Gunicorn graceful shutdown (30 seconds)
   - Old tasks terminated

6. **Repeat Process**
   - Process repeats for remaining task batches
   - For 4-task service: 2 deployment waves
   - Total deployment time: 5-10 minutes

### 7.3 Deployment Command

**Update ECS Service with New Image:**

```bash
# Set variables
CLUSTER="jiratest-error-triage-production-cluster"
SERVICE="jiratest-error-triage-production"
NEW_IMAGE_TAG="v1.2.3"  # Or Git SHA, e.g., "a1b2c3d4"

# Update task definition with new image
TASK_DEFINITION=$(aws ecs describe-task-definition \
  --task-definition jiratest-error-triage-production \
  --query 'taskDefinition' \
  --output json)

NEW_TASK_DEF=$(echo $TASK_DEFINITION | jq \
  --arg IMAGE "${ECR_REGISTRY}/jiratest/error-triage:${NEW_IMAGE_TAG}" \
  '.containerDefinitions[0].image = $IMAGE | del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities, .registeredAt, .registeredBy)')

# Register new task definition revision
NEW_REVISION=$(aws ecs register-task-definition \
  --cli-input-json "$NEW_TASK_DEF" \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)

echo "New task definition: $NEW_REVISION"

# Update ECS service to use new task definition
aws ecs update-service \
  --cluster $CLUSTER \
  --service $SERVICE \
  --task-definition $NEW_REVISION \
  --force-new-deployment

# Wait for deployment to stabilize
echo "Waiting for service to stabilize..."
aws ecs wait services-stable \
  --cluster $CLUSTER \
  --services $SERVICE

echo "Deployment complete!"
```

### 7.4 Monitor Deployment

```bash
# Watch deployment events
aws ecs describe-services \
  --cluster $CLUSTER \
  --services $SERVICE \
  --query 'services[0].events[:10]' \
  --output table

# Monitor running tasks
watch -n 5 "aws ecs list-tasks \
  --cluster $CLUSTER \
  --service-name $SERVICE \
  --query 'taskArns' \
  --output table"

# Check task health
aws ecs describe-tasks \
  --cluster $CLUSTER \
  --tasks $(aws ecs list-tasks --cluster $CLUSTER --service-name $SERVICE --query 'taskArns' --output text) \
  --query 'tasks[*].[taskArn,lastStatus,healthStatus]' \
  --output table
```

### 7.5 Deployment Circuit Breaker

**Automatic Rollback Triggers:**

The ECS deployment circuit breaker automatically rolls back if:

1. **Health Check Failures**: New tasks fail ALB health checks after 2 consecutive attempts
2. **Task Crash**: New tasks repeatedly crash or fail to start
3. **Threshold Exceeded**: More than 50% of tasks fail within 10 minutes

**Circuit Breaker Configuration (in Terraform):**

```hcl
deployment_circuit_breaker {
  enable   = true
  rollback = true
}

deployment_configuration {
  minimum_healthy_percent = 100
  maximum_percent         = 200
}
```

**Manual Intervention Required If:**
- Circuit breaker triggers automatic rollback
- Deployment stuck in "in progress" state for > 30 minutes
- Application logs show errors post-deployment

---

## 8. Environment-Specific Configurations

### 8.1 Development Environment

**Purpose**: Rapid iteration for developers

| Configuration | Value | Rationale |
|---------------|-------|-----------|
| **ECS Tasks** | 1 task | Minimal resources for cost efficiency |
| **ECS CPU/Memory** | 256 CPU / 512 MB | Sufficient for low-volume testing |
| **Redis** | `cache.t4g.micro` (single node) | Minimal caching layer |
| **MongoDB** | Disabled (`ENABLE_MONGO=false`) | Simplify dev environment |
| **Auto-Scaling** | Disabled | Fixed capacity |
| **Log Level** | `DEBUG` | Verbose logging for troubleshooting |
| **Rate Limits** | 50 req/min | Relaxed for testing |
| **Deployment** | Manual trigger | Deploy on-demand |

**Terraform Variables (`environments/dev.tfvars`):**

```hcl
environment            = "dev"
ecs_desired_count      = 1
ecs_min_count          = 1
ecs_max_count          = 1
ecs_task_cpu           = "256"
ecs_task_memory        = "512"
redis_node_type        = "cache.t4g.micro"
redis_num_cache_nodes  = 1
enable_mongodb         = false
log_level              = "DEBUG"
docker_image_tag       = "dev-latest"
```

### 8.2 Staging Environment

**Purpose**: Pre-production validation and integration testing

| Configuration | Value | Rationale |
|---------------|-------|-----------|
| **ECS Tasks** | 2 tasks (min 1, max 4) | Mirror production architecture |
| **ECS CPU/Memory** | 512 CPU / 1024 MB | Realistic production workload |
| **Redis** | `cache.t4g.small` (2 nodes, multi-AZ) | High availability testing |
| **MongoDB** | Enabled (`ENABLE_MONGO=true`) | Full feature parity with production |
| **Auto-Scaling** | Enabled (target 70% CPU) | Test scaling behavior |
| **Log Level** | `INFO` | Production-like logging |
| **Rate Limits** | 100 req/min | Match production settings |
| **Deployment** | Automatic on merge to `main` | Continuous deployment |

**Terraform Variables (`environments/staging.tfvars`):**

```hcl
environment            = "staging"
ecs_desired_count      = 2
ecs_min_count          = 1
ecs_max_count          = 4
ecs_task_cpu           = "512"
ecs_task_memory        = "1024"
redis_node_type        = "cache.t4g.small"
redis_num_cache_nodes  = 2
enable_mongodb         = true
log_level              = "INFO"
docker_image_tag       = "staging-latest"
```

### 8.3 Production Environment

**Purpose**: Live service with high availability and performance

| Configuration | Value | Rationale |
|---------------|-------|-----------|
| **ECS Tasks** | 4 tasks (min 2, max 8) | Handle peak traffic with redundancy |
| **ECS CPU/Memory** | 512 CPU / 1024 MB | Optimized for sustained load |
| **Redis** | `cache.t4g.medium` (3 nodes, multi-AZ) | High throughput, automatic failover |
| **MongoDB** | Enabled (`ENABLE_MONGO=true`, M10 cluster) | Audit trail and compliance |
| **Auto-Scaling** | Enabled (target 70% CPU, custom metrics) | Dynamic capacity management |
| **Log Level** | `INFO` (ERROR only for sensitive paths) | Balanced visibility and volume |
| **Rate Limits** | 100 req/min (strict enforcement) | Protect against abuse |
| **Deployment** | Manual approval required | Controlled production changes |

**Terraform Variables (`environments/production.tfvars`):**

```hcl
environment            = "production"
ecs_desired_count      = 4
ecs_min_count          = 2
ecs_max_count          = 8
ecs_task_cpu           = "512"
ecs_task_memory        = "1024"
redis_node_type        = "cache.t4g.medium"
redis_num_cache_nodes  = 3
enable_mongodb         = true
log_level              = "INFO"
docker_image_tag       = "prod-latest"
```

### 8.4 Configuration Differences Summary

| Aspect | Development | Staging | Production |
|--------|-------------|---------|------------|
| **Availability** | Single-AZ | Multi-AZ | Multi-AZ |
| **Task Count** | 1 | 2-4 | 4-8 |
| **Redis** | Micro, 1 node | Small, 2 nodes | Medium, 3 nodes |
| **MongoDB** | Disabled | M10 cluster | M10 cluster |
| **Auto-Scaling** | No | Yes | Yes |
| **Monitoring** | Basic | Full | Full + Alerting |
| **Deployment** | Manual | Automatic | Manual Approval |
| **Cost** | ~$50/month | ~$200/month | ~$500/month |

---

## 9. Post-Deployment Verification

### 9.1 Health Check Validation

**Step 1: Check Health Endpoint**

```bash
# Basic health check
curl -f https://error-triage.jiratest.com/healthz

# Expected Response (HTTP 200):
{
  "status": "healthy",
  "timestamp": "2025-10-07T12:34:56.789Z",
  "dependencies": {
    "redis": {
      "status": "healthy",
      "latency_ms": 2
    },
    "mongodb": {
      "status": "healthy",
      "latency_ms": 15
    },
    "jira": {
      "status": "healthy",
      "latency_ms": 85
    }
  },
  "version": "1.2.3"
}
```

**Step 2: Parse and Validate Dependencies**

```bash
# Extract dependency statuses
HEALTH_JSON=$(curl -s https://error-triage.jiratest.com/healthz)

REDIS_STATUS=$(echo $HEALTH_JSON | jq -r '.dependencies.redis.status')
MONGODB_STATUS=$(echo $HEALTH_JSON | jq -r '.dependencies.mongodb.status')
JIRA_STATUS=$(echo $HEALTH_JSON | jq -r '.dependencies.jira.status')

echo "Redis: $REDIS_STATUS"
echo "MongoDB: $MONGODB_STATUS"
echo "Jira: $JIRA_STATUS"

# Verify all healthy
if [ "$REDIS_STATUS" = "healthy" ] && [ "$JIRA_STATUS" = "healthy" ]; then
  echo "✓ All required dependencies healthy"
else
  echo "✗ Dependency failures detected"
  exit 1
fi
```

### 9.2 Metrics Endpoint Validation

```bash
# Fetch Prometheus metrics
curl https://error-triage.jiratest.com/metrics

# Verify required counters exist
curl -s https://error-triage.jiratest.com/metrics | grep -E "(events_received_total|jira_issues_created_total|jira_comments_added_total)"

# Expected output (example):
# events_received_total{source="vercel",environment="production"} 1234
# events_received_total{source="gcp",environment="production"} 567
# jira_issues_created_total{environment="production",project="ET"} 89
# jira_comments_added_total{environment="production",project="ET"} 1045

# Verify histograms exist
curl -s https://error-triage.jiratest.com/metrics | grep event_processing_duration_seconds

# Expected output:
# event_processing_duration_seconds_bucket{le="0.1"} 1000
# event_processing_duration_seconds_bucket{le="0.5"} 1150
# event_processing_duration_seconds_sum 125.5
# event_processing_duration_seconds_count 1200
```

### 9.3 CloudWatch Logs Validation

```bash
# Tail CloudWatch Logs
aws logs tail /aws/ecs/jiratest-error-triage-staging --follow

# Query recent logs (last 5 minutes)
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-staging \
  --start-time $(($(date +%s) - 300))000 \
  --query 'events[*].message' \
  --output text | head -10

# Verify JSON structure
FIRST_LOG=$(aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-staging \
  --start-time $(($(date +%s) - 300))000 \
  --limit 1 \
  --query 'events[0].message' \
  --output text)

echo $FIRST_LOG | jq

# Expected fields:
# {
#   "timestamp": "2025-10-07T12:34:56.789Z",
#   "level": "INFO",
#   "service": "error-triage",
#   "environment": "staging",
#   "message": "Application started",
#   "version": "1.2.3"
# }

# Check for ERROR level logs
ERROR_COUNT=$(aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-staging \
  --start-time $(($(date +%s) - 300))000 \
  --filter-pattern '{ $.level = "ERROR" }' \
  --query 'events' \
  --output json | jq '. | length')

if [ "$ERROR_COUNT" -eq 0 ]; then
  echo "✓ No ERROR-level logs in last 5 minutes"
else
  echo "⚠ Found $ERROR_COUNT ERROR-level logs"
fi
```

### 9.4 Submit Test Webhook

**Vercel Webhook Test:**

```bash
# Set variables
SERVICE_URL="https://error-triage.jiratest.com"
VERCEL_SECRET="your-webhook-secret"  # From Secrets Manager

# Create test payload
PAYLOAD=$(cat <<EOF
{
  "id": "test-event-$(date +%s)",
  "type": "deployment.error",
  "deployment": {
    "id": "dpl_test123",
    "url": "my-app-test.vercel.app"
  },
  "payload": {
    "errors": [{
      "name": "TestError",
      "message": "This is a test error for deployment validation",
      "stack": "TestError: Test error\\n    at testFunction (test.js:10:15)\\n    at Object.<anonymous> (test.js:20:5)"
    }]
  },
  "environment": "production",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
}
EOF
)

# Compute HMAC signature
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$VERCEL_SECRET" | awk '{print $2}')

# Send webhook
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${SERVICE_URL}/events" \
  -H "Content-Type: application/json" \
  -H "x-vercel-signature: $SIGNATURE" \
  -d "$PAYLOAD")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$HTTP_CODE" -eq 202 ]; then
  echo "✓ Webhook accepted (HTTP 202)"
  echo "Response: $RESPONSE_BODY"
else
  echo "✗ Webhook failed (HTTP $HTTP_CODE)"
  echo "Response: $RESPONSE_BODY"
  exit 1
fi
```

**GCP Webhook Test:**

```bash
# GCP webhooks use OIDC JWT, which is more complex to generate
# Use gcloud to publish a test message to Pub/Sub

gcloud pubsub topics publish error-events \
  --message '{
    "severity": "ERROR",
    "textPayload": "Test error from GCP deployment validation",
    "resource": {
      "type": "cloud_run_revision",
      "labels": {
        "service_name": "test-service",
        "revision_name": "test-service-00001"
      }
    },
    "insertId": "test-'$(date +%s)'",
    "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)'"
  }'
```

### 9.5 Verify Jira Issue Creation

```bash
# Wait a few seconds for processing
sleep 5

# Check Jira for test issue
# (Manual verification in Jira UI or via API)

# Example Jira API query (requires jira-cli or curl with auth)
curl -u "your-email@company.com:$JIRA_API_TOKEN" \
  "https://your-org.atlassian.net/rest/api/3/search?jql=project=ET AND labels=source:vercel AND created >= -5m" | jq '.issues[0].key'

# Expected output:
# "ET-1234"

# Verify issue details
echo "✓ Test Jira issue created"
echo "   Check Jira UI: https://your-org.atlassian.net/browse/ET-1234"
```

### 9.6 Verify Redis Connectivity

```bash
# Connect to Redis via bastion host or port forwarding
# (Assuming you have Redis CLI access)

# Check frequency counter
redis-cli -h <redis-endpoint> -p 6379 GET "freq:production:<fingerprint>"

# Expected output: Counter value (e.g., "1" for first occurrence)

# Check deduplication cache
redis-cli -h <redis-endpoint> -p 6379 GET "dedup:test-event-<timestamp>"

# Expected output: "1" if event was deduplicated
```

### 9.7 Verify MongoDB Audit Trail (if enabled)

```bash
# Connect to MongoDB Atlas
mongosh "mongodb+srv://cluster.mongodb.net/jiratest-staging" \
  --username error-triage \
  --password <password>

# Query error_events collection
db.error_events.find({event_id: /test-event/}).pretty()

# Expected output:
# {
#   "_id": ObjectId("..."),
#   "event_id": "test-event-1696694096",
#   "fingerprint": "a3f5b9c8d2e1...",
#   "source": "vercel",
#   "service": "test-service",
#   "environment": "production",
#   "error_class": "TestError",
#   "message": "This is a test error...",
#   "occurred_at": ISODate("2025-10-07T12:34:56.789Z"),
#   "jira_issue_key": "ET-1234",
#   "created_at": ISODate("2025-10-07T12:35:01.000Z")
# }
```

---

## 10. Rollback Procedures

### 10.1 Identify Rollback Trigger

Common reasons for rollback:

1. **Health Check Failures**: `/healthz` returns 503 after deployment
2. **High Error Rate**: CloudWatch metrics show error spike > 5%
3. **Application Crashes**: Tasks repeatedly crash or restart
4. **Dependency Failures**: Redis/MongoDB/Jira connectivity issues
5. **Performance Degradation**: Response times exceed SLA (p95 > 300ms)

### 10.2 Immediate Rollback (< 5 minutes old deployment)

**Method A: ECS Service Update to Previous Task Definition**

```bash
# Set variables
CLUSTER="jiratest-error-triage-production-cluster"
SERVICE="jiratest-error-triage-production"

# Get current task definition
CURRENT_TASK_DEF=$(aws ecs describe-services \
  --cluster $CLUSTER \
  --services $SERVICE \
  --query 'services[0].taskDefinition' \
  --output text)

echo "Current task definition: $CURRENT_TASK_DEF"
# Example output: jiratest-error-triage-production:16

# List recent task definitions
aws ecs list-task-definitions \
  --family-prefix jiratest-error-triage-production \
  --status ACTIVE \
  --sort DESC \
  --max-items 5

# Identify previous revision (e.g., revision 15)
PREVIOUS_REVISION="jiratest-error-triage-production:15"

# Rollback to previous task definition
echo "Rolling back to $PREVIOUS_REVISION..."
aws ecs update-service \
  --cluster $CLUSTER \
  --service $SERVICE \
  --task-definition $PREVIOUS_REVISION \
  --force-new-deployment

# Wait for rollback to complete
echo "Waiting for service to stabilize..."
aws ecs wait services-stable \
  --cluster $CLUSTER \
  --services $SERVICE

echo "✓ Rollback complete!"
```

**Verify Rollback:**

```bash
# Check service status
aws ecs describe-services \
  --cluster $CLUSTER \
  --services $SERVICE \
  --query 'services[0].[taskDefinition,runningCount,desiredCount]' \
  --output table

# Test health check
curl -f https://error-triage.jiratest.com/healthz

# Monitor error rate
# (Check CloudWatch dashboard or metrics)
```

### 10.3 Git-Based Rollback (Terraform Revert)

**Method B: Revert Git Commit and Redeploy**

```bash
# Navigate to repository
cd jiratest-error-triage

# View recent commits
git log --oneline -10

# Identify commit to revert (e.g., abc1234)
COMMIT_TO_REVERT="abc1234"

# Revert the commit
git revert $COMMIT_TO_REVERT

# Push revert to main branch
git push origin main

# CI/CD pipeline automatically triggers:
# 1. Build Docker image with reverted code
# 2. Push to ECR
# 3. Update ECS service with new task definition

# Monitor deployment via GitHub Actions or ECS console
```

### 10.4 Docker Image Rollback

**Method C: Rollback to Previous Docker Image Tag**

```bash
# List recent image tags
aws ecr describe-images \
  --repository-name jiratest/error-triage \
  --query 'sort_by(imageDetails, &imagePushedAt)[-5:] | reverse(@) | [*].[imageTags[0],imagePushedAt]' \
  --output table

# Identify previous stable image tag
PREVIOUS_IMAGE_TAG="v1.2.2"  # Or Git SHA from stable deployment

# Update ECS service to use previous image
# (Follow Method A but with modified task definition image)

# Get current task definition JSON
TASK_DEF_JSON=$(aws ecs describe-task-definition \
  --task-definition jiratest-error-triage-production \
  --query 'taskDefinition')

# Modify image tag
NEW_TASK_DEF=$(echo $TASK_DEF_JSON | jq \
  --arg IMAGE "${ECR_REGISTRY}/jiratest/error-triage:${PREVIOUS_IMAGE_TAG}" \
  '.containerDefinitions[0].image = $IMAGE | del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities, .registeredAt, .registeredBy)')

# Register new task definition
NEW_REVISION=$(aws ecs register-task-definition \
  --cli-input-json "$NEW_TASK_DEF" \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)

# Update service
aws ecs update-service \
  --cluster $CLUSTER \
  --service $SERVICE \
  --task-definition $NEW_REVISION \
  --force-new-deployment
```

### 10.5 Configuration Rollback

**Rollback Configuration File Changes:**

```bash
# If severity_rules.yaml or ownership_rules.yaml changed

# Identify previous version in Git
git log --oneline config/severity_rules.yaml

# Restore previous version
git checkout <commit-hash> config/severity_rules.yaml

# Commit and push
git commit -m "Rollback severity rules to stable configuration"
git push origin main

# Restart ECS tasks to reload configuration
aws ecs update-service \
  --cluster $CLUSTER \
  --service $SERVICE \
  --force-new-deployment

# Or send SIGHUP signal for hot-reload (if implemented)
# (Requires connecting to ECS task and sending signal)
```

### 10.6 Database Rollback (Not Applicable)

**MongoDB Collections:**

The Error Triage service uses schema-less MongoDB with **append-only** collections:
- `error_events`: Audit trail (no updates, only inserts)
- `jira_actions`: Action log (no updates, only inserts)

**No database migrations or schema changes require rollback.**

If data corruption occurs:
1. Identify corrupt documents via `_id` or `created_at` timestamp
2. Delete corrupt documents: `db.error_events.deleteMany({created_at: {$gte: ISODate("2025-10-07T12:00:00Z")}})`
3. Reprocess events from Vercel/GCP logs if needed

### 10.7 Post-Rollback Validation

**Validation Checklist:**

- [ ] Health checks passing (`/healthz` returns HTTP 200)
- [ ] Error rate returned to normal (< 1% in CloudWatch)
- [ ] Task count matches desired count
- [ ] No task crashes or restarts
- [ ] Submit test webhook and verify Jira issue created
- [ ] Monitor CloudWatch Logs for ERROR-level entries

**Incident Response:**

1. **Create Post-Mortem Document**
   - Incident timeline
   - Root cause analysis
   - Corrective actions

2. **Implement Fix**
   - Create feature branch with fix
   - Add additional tests
   - Deploy to staging for validation

3. **Redeploy When Ready**
   - Ensure fix verified in staging
   - Schedule production deployment with team notification
   - Monitor closely post-deployment

---

## 11. CI/CD Pipeline Integration

### 11.1 GitHub Actions Workflows

The repository includes four GitHub Actions workflows for automated CI/CD:

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| **Continuous Integration** | `.github/workflows/ci.yml` | Pull request, push to branches | Lint, test, security scan |
| **Build and Push** | `.github/workflows/build-push.yml` | Merge to `main`, release tags | Build Docker image, push to ECR |
| **Deploy** | `.github/workflows/deploy.yml` | After build-push, manual dispatch | Terraform apply, ECS update |
| **Integration Tests** | `.github/workflows/test-integration.yml` | After deployment to staging | End-to-end smoke tests |

### 11.2 Deployment Triggers

**Automatic Deployments:**

| Branch/Tag | Environment | Approval Required | Validation |
|------------|-------------|-------------------|------------|
| **Merge to `main`** | Staging | No | Automatic smoke tests |
| **Tag `v*.*.*`** | Production | Yes (2 approvers) | Manual validation + smoke tests |

**Manual Deployments:**

```bash
# Trigger manual deployment via GitHub Actions
# Navigate to: https://github.com/your-org/jiratest-error-triage/actions/workflows/deploy.yml
# Click "Run workflow"
# Select environment: staging | production
# Enter Docker image tag: v1.2.3 or Git SHA
```

### 11.3 Approval Gates

**Production Deployment Approval:**

1. GitHub Actions workflow pauses at approval step
2. Notification sent to deployment approvers via GitHub
3. Approvers review:
   - Terraform plan output
   - Staging test results
   - Release notes
4. Require 2 approvals from:
   - Tech lead
   - Product owner
   - DevOps engineer
5. Workflow resumes after approvals

**Configure Approvers:**

```yaml
# .github/workflows/deploy.yml (excerpt)
jobs:
  deploy-production:
    environment:
      name: production
      url: https://error-triage.jiratest.com
    # Approvals configured in GitHub repository settings:
    # Settings > Environments > production > Required reviewers
```

### 11.4 Deployment Notifications

**Slack Integration:**

```bash
# Configure Slack webhook in GitHub Secrets
# Add SLACK_WEBHOOK_URL to repository secrets

# Notification sent on:
# - Deployment start
# - Deployment success
# - Deployment failure
# - Rollback triggered

# Example Slack message:
# 🚀 Deployment to production started
# Service: error-triage
# Version: v1.2.3
# Deployed by: @username
# ECS Cluster: jiratest-error-triage-production-cluster
```

---

## 12. Troubleshooting

### 12.1 Tasks Fail to Start

**Symptoms:**
- ECS tasks in "STOPPED" state immediately after launch
- No healthy tasks in target group

**Diagnosis:**

```bash
# List recent stopped tasks
aws ecs list-tasks \
  --cluster $CLUSTER \
  --service-name $SERVICE \
  --desired-status STOPPED \
  --query 'taskArns[:5]' \
  --output text

# Describe stopped task
STOPPED_TASK="arn:aws:ecs:..."
aws ecs describe-tasks \
  --cluster $CLUSTER \
  --tasks $STOPPED_TASK \
  --query 'tasks[0].stopCode'

# Check CloudWatch Logs for errors
aws logs tail /aws/ecs/jiratest-error-triage-staging \
  --filter-pattern "ERROR" \
  --since 5m
```

**Common Causes and Fixes:**

| Cause | Error Message | Fix |
|-------|---------------|-----|
| **Secrets Manager access denied** | `AccessDeniedException` | Verify ECS task role has `secretsmanager:GetSecretValue` permission |
| **Invalid secret ARN** | `ResourceNotFoundException` | Check secret ARN in Terraform variables matches Secrets Manager |
| **Redis connection failure** | `ECONNREFUSED` or `Redis connection timeout` | Verify Redis endpoint, security group allows port 6379 from ECS |
| **Missing environment variable** | `KeyError` or `ConfigurationError` | Check ECS task definition includes all required env vars |
| **Python dependency error** | `ModuleNotFoundError` | Rebuild Docker image, verify `requirements.txt` includes all dependencies |

### 12.2 Health Checks Failing

**Symptoms:**
- `/healthz` returns HTTP 503
- ALB marks tasks as unhealthy
- Tasks continuously restart

**Diagnosis:**

```bash
# Test health endpoint directly
curl -v https://error-triage.jiratest.com/healthz

# Check dependency status in response
curl -s https://error-triage.jiratest.com/healthz | jq '.dependencies'

# Expected output:
# {
#   "redis": {"status": "unhealthy", "error": "Connection timeout"},
#   "mongodb": {"status": "healthy", "latency_ms": 20},
#   "jira": {"status": "healthy", "latency_ms": 100}
# }
```

**Fixes by Dependency:**

**Redis Unhealthy:**
```bash
# Verify Redis cluster status
aws elasticache describe-cache-clusters \
  --cache-cluster-id jiratest-error-triage-staging-redis \
  --show-cache-node-info

# Check security group allows ECS → Redis on port 6379
aws ec2 describe-security-groups \
  --group-ids <redis-sg-id> \
  --query 'SecurityGroups[0].IpPermissions'

# Test Redis connectivity from ECS task
# (Requires ECS Exec or bastion host)
redis-cli -h <redis-endpoint> -p 6379 PING
```

**MongoDB Unhealthy:**
```bash
# Verify MongoDB Atlas network access allows AWS VPC CIDR
# MongoDB Atlas Console > Network Access > Add IP Access List Entry
# Add CIDR: 10.0.0.0/16 (or specific private subnet CIDRs)

# Test MongoDB connection string
mongosh "mongodb+srv://..." --eval "db.adminCommand('ping')"
```

**Jira Unhealthy:**
```bash
# Verify Jira credentials in Secrets Manager
aws secretsmanager get-secret-value \
  --secret-id jira/jiratest/staging/credentials \
  --query 'SecretString' \
  --output text | jq

# Test Jira API connectivity
curl -u "email@company.com:$API_TOKEN" \
  "https://your-org.atlassian.net/rest/api/3/serverInfo"

# Check NAT Gateway allows outbound HTTPS
# Verify security group outbound rules allow 0.0.0.0/0 on port 443
```

### 12.3 High Memory/CPU Usage

**Symptoms:**
- ECS tasks using > 80% of allocated memory
- CPU throttling visible in CloudWatch Container Insights
- Tasks killed due to out-of-memory (OOM)

**Diagnosis:**

```bash
# View memory/CPU metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/ECS \
  --metric-name MemoryUtilization \
  --dimensions Name=ServiceName,Value=$SERVICE Name=ClusterName,Value=$CLUSTER \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Maximum

# Check for memory leaks in application logs
aws logs filter-log-events \
  --log-group-name /aws/ecs/jiratest-error-triage-staging \
  --filter-pattern "MemoryError" \
  --start-time $(($(date +%s) - 3600))000
```

**Fixes:**

**Increase Task Resources:**
```hcl
# Update environments/staging.tfvars
ecs_task_cpu    = "1024"  # Increase from 512
ecs_task_memory = "2048"  # Increase from 1024

# Apply Terraform changes
terraform apply -var-file=environments/staging.tfvars
```

**Optimize Gunicorn Workers:**
```dockerfile
# Reduce worker count or threads if memory constrained
CMD ["gunicorn", \
     "--workers", "2",  # Reduce from 4
     "--threads", "1",  # Reduce from 2
     ...]
```

**Investigate Memory Leaks:**
- Review CloudWatch Logs for repeated connection openings without closing
- Check Redis connection pool is properly configured
- Verify MongoDB client uses connection pooling

### 12.4 Connection Timeouts to External Services

**Symptoms:**
- Jira API calls timeout (> 10 seconds)
- Webhook requests fail with 504 Gateway Timeout

**Diagnosis:**

```bash
# Check ALB timeout settings
aws elbv2 describe-target-groups \
  --target-group-arns <target-group-arn> \
  --query 'TargetGroups[0].[HealthCheckTimeoutSeconds,HealthCheckIntervalSeconds]'

# Verify NAT Gateway is functional
# (Check NAT Gateway metrics in CloudWatch)

# Test external connectivity from ECS task
# (Requires ECS Exec)
curl -v --max-time 10 https://your-org.atlassian.net/rest/api/3/serverInfo
```

**Fixes:**

**Increase Timeout Configuration:**
```python
# src/services/jira_integration.py
jira = JIRA(
    server=jira_base_url,
    basic_auth=(email, api_token),
    timeout=15  # Increase from default 10 seconds
)
```

**Check NAT Gateway Capacity:**
```bash
# Verify NAT Gateway not throttling connections
# If necessary, increase to NAT Gateway per AZ
# (Configure in Terraform VPC module)
```

### 12.5 Terraform State Conflicts

**Symptoms:**
- `terraform apply` fails with "Error acquiring the state lock"
- Multiple team members running Terraform simultaneously

**Diagnosis:**

```bash
# Check DynamoDB lock table
aws dynamodb scan \
  --table-name jiratest-terraform-locks \
  --projection-expression "LockID,Info"
```

**Fix:**

```bash
# If lock is stale (process crashed), force unlock
terraform force-unlock <lock-id>

# Prevent future conflicts:
# 1. Use CI/CD for all Terraform changes
# 2. Coordinate manual Terraform runs with team
# 3. Use workspace isolation (dev/staging/prod)
```

### 12.6 Docker Image Build Failures

**Symptoms:**
- CI/CD pipeline fails at Docker build step
- Trivy vulnerability scanner blocks deployment

**Common Errors:**

**Pip Install Fails:**
```bash
# Error: Could not find a version that satisfies the requirement Flask==3.1.2

# Fix: Verify requirements.txt versions exist on PyPI
# Or update to available versions:
pip index versions Flask  # Check available versions
```

**Trivy Blocks Deployment (Critical CVE):**
```bash
# Error: Critical vulnerabilities found in image

# Identify vulnerable packages
docker run --rm aquasec/trivy image \
  jiratest/error-triage:latest

# Fix: Update vulnerable packages in requirements.txt
# Or request security exception (see Section 3.7 in Agent Action Plan)
```

### 12.7 Getting Help

**Resources:**

- **Documentation**: `docs/` directory in repository
- **Runbook**: `docs/runbook.md` for operational procedures
- **Monitoring Guide**: `docs/monitoring.md` for observability
- **Architecture Diagram**: `docs/architecture.md` for system design

**Support Channels:**

- **Slack**: `#error-triage-support`
- **Email**: devops@yourcompany.com
- **On-Call**: PagerDuty rotation for critical issues

**Creating Support Ticket:**

Include the following information:
1. Environment (dev/staging/production)
2. Time of issue (UTC timestamp)
3. Error messages from CloudWatch Logs
4. ECS task ARNs affected
5. Steps already attempted
6. Impact on service (partial/full outage)

---

## Appendix A: Quick Reference Commands

### Service Status

```bash
# Check ECS service
aws ecs describe-services \
  --cluster jiratest-error-triage-staging-cluster \
  --services jiratest-error-triage-staging

# Health check
curl https://error-triage.jiratest.com/healthz

# Metrics
curl https://error-triage.jiratest.com/metrics

# CloudWatch logs
aws logs tail /aws/ecs/jiratest-error-triage-staging --follow
```

### Deployment

```bash
# Update service with new image
aws ecs update-service \
  --cluster <cluster> \
  --service <service> \
  --force-new-deployment

# Terraform deploy
cd deploy/terraform
terraform workspace select staging
terraform plan -var-file=environments/staging.tfvars -out=tfplan
terraform apply tfplan
```

### Rollback

```bash
# Rollback to previous task definition
aws ecs update-service \
  --cluster <cluster> \
  --service <service> \
  --task-definition <previous-revision>
```

### Secrets

```bash
# Retrieve secret
aws secretsmanager get-secret-value \
  --secret-id jira/jiratest/staging/credentials

# Update secret
aws secretsmanager update-secret \
  --secret-id jira/jiratest/staging/credentials \
  --secret-string '{...}'
```

---

## Appendix B: Environment Variables Reference

| Variable | Description | Example | Required |
|----------|-------------|---------|----------|
| `REDIS_HOST` | Redis endpoint | `cluster.cache.amazonaws.com` | Yes |
| `REDIS_PORT` | Redis port | `6379` | Yes |
| `JIRA_CREDENTIALS_SECRET_ARN` | ARN of Jira credentials secret | `arn:aws:secretsmanager:...` | Yes |
| `WEBHOOK_SECRET_ARN` | ARN of webhook secrets | `arn:aws:secretsmanager:...` | Yes |
| `MONGODB_SECRET_ARN` | ARN of MongoDB connection string | `arn:aws:secretsmanager:...` | No |
| `ENABLE_MONGO` | Enable MongoDB audit logging | `true` or `false` | No (default: false) |
| `LOG_LEVEL` | Application log level | `DEBUG`, `INFO`, `ERROR` | No (default: INFO) |
| `AWS_REGION` | AWS region | `us-east-1` | Yes |
| `ENVIRONMENT` | Deployment environment | `dev`, `staging`, `production` | Yes |

---

**Document Version**: 1.0
**Last Updated**: 2025-10-07
**Maintained By**: DevOps Team
