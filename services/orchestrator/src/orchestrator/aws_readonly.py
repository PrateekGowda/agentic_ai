"""Read-only AWS account inspector.

All methods call only *Describe / List / Get* APIs.
No create, update, or delete calls are made here.
"""
from __future__ import annotations

from typing import Any

import boto3


def query_aws_account(message: str, region: str = "us-east-1") -> dict[str, Any]:
    """Dispatch an AWS read-only query based on message intent.

    Returns {answer: str, data: list|dict} so the orchestrator can
    embed the answer as a chat message.
    """
    lower = message.lower()

    try:
        if any(k in lower for k in ("s3 bucket", "s3", "bucket")):
            return _list_s3_buckets(region)
        if any(k in lower for k in ("lambda", "function")):
            return _list_lambdas(region)
        if any(k in lower for k in ("ec2", "instance", "server", "vm")):
            return _list_ec2_instances(region)
        if any(k in lower for k in ("vpc", "network")):
            return _list_vpcs(region)
        if any(k in lower for k in ("rds", "database", "db")):
            return _list_rds(region)
        if any(k in lower for k in ("ecs", "container", "service", "cluster")):
            return _list_ecs(region)
        if any(k in lower for k in ("ecr", "image", "repository")):
            return _list_ecr(region)
        if any(k in lower for k in ("iam role", "role")):
            return _list_iam_roles()
        if any(k in lower for k in ("cloudwatch", "alarm")):
            return _list_cloudwatch_alarms(region)
        if any(k in lower for k in ("cloudformation", "stack", "cfn")):
            return _list_cfn_stacks(region)
        if any(k in lower for k in ("dynamodb", "dynamo", "table")):
            return _list_dynamodb_tables(region)
        if any(k in lower for k in ("secret", "secrets manager")):
            return _list_secrets(region)
        if any(k in lower for k in ("codebuild", "build")):
            return _list_codebuild_projects(region)
        if any(k in lower for k in ("sns", "topic", "notification")):
            return _list_sns_topics(region)
        if any(k in lower for k in ("sqs", "queue")):
            return _list_sqs_queues(region)
        if any(k in lower for k in ("cost", "billing", "account")):
            return _account_summary()
        return _account_summary()
    except Exception as exc:
        return {"answer": f"I queried AWS but got an error: {exc}", "data": []}


def _fmt(items: list[str], noun: str) -> str:
    if not items:
        return f"No {noun} found in this account/region."
    return f"Found {len(items)} {noun}:\n" + "\n".join(f"  - {i}" for i in items[:50])


def _list_s3_buckets(region: str) -> dict[str, Any]:
    client = boto3.client("s3", region_name=region)
    resp = client.list_buckets()
    names = [b["Name"] for b in resp.get("Buckets", [])]
    return {"answer": _fmt(names, "S3 buckets"), "data": names}


def _list_lambdas(region: str) -> dict[str, Any]:
    client = boto3.client("lambda", region_name=region)
    paginator = client.get_paginator("list_functions")
    functions = []
    for page in paginator.paginate():
        for f in page.get("Functions", []):
            functions.append(f"{f['FunctionName']} ({f['Runtime']}, {f.get('Description', 'no description')})")
    return {"answer": _fmt(functions, "Lambda functions"), "data": functions}


def _list_ec2_instances(region: str) -> dict[str, Any]:
    client = boto3.client("ec2", region_name=region)
    resp = client.describe_instances()
    instances = []
    for reservation in resp.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "unnamed")
            instances.append(
                f"{inst['InstanceId']} ({inst['InstanceType']}, {inst['State']['Name']}, name={name})"
            )
    return {"answer": _fmt(instances, "EC2 instances"), "data": instances}


def _list_vpcs(region: str) -> dict[str, Any]:
    client = boto3.client("ec2", region_name=region)
    resp = client.describe_vpcs()
    vpcs = [
        f"{v['VpcId']} (CIDR={v['CidrBlock']}, default={v.get('IsDefault', False)})"
        for v in resp.get("Vpcs", [])
    ]
    return {"answer": _fmt(vpcs, "VPCs"), "data": vpcs}


def _list_rds(region: str) -> dict[str, Any]:
    client = boto3.client("rds", region_name=region)
    resp = client.describe_db_instances()
    dbs = [
        f"{db['DBInstanceIdentifier']} ({db['DBInstanceClass']}, {db['DBInstanceStatus']}, engine={db['Engine']})"
        for db in resp.get("DBInstances", [])
    ]
    return {"answer": _fmt(dbs, "RDS instances"), "data": dbs}


def _list_ecs(region: str) -> dict[str, Any]:
    client = boto3.client("ecs", region_name=region)
    clusters = client.list_clusters().get("clusterArns", [])
    items = []
    for arn in clusters[:10]:
        name = arn.split("/")[-1]
        services = client.list_services(cluster=arn).get("serviceArns", [])
        items.append(f"{name} ({len(services)} services)")
    return {"answer": _fmt(items, "ECS clusters"), "data": items}


def _list_ecr(region: str) -> dict[str, Any]:
    client = boto3.client("ecr", region_name=region)
    resp = client.describe_repositories()
    repos = [r["repositoryName"] for r in resp.get("repositories", [])]
    return {"answer": _fmt(repos, "ECR repositories"), "data": repos}


def _list_iam_roles() -> dict[str, Any]:
    client = boto3.client("iam")
    paginator = client.get_paginator("list_roles")
    roles = []
    for page in paginator.paginate():
        for r in page.get("Roles", []):
            roles.append(r["RoleName"])
    return {"answer": _fmt(roles, "IAM roles"), "data": roles}


def _list_cloudwatch_alarms(region: str) -> dict[str, Any]:
    client = boto3.client("cloudwatch", region_name=region)
    resp = client.describe_alarms()
    alarms = [
        f"{a['AlarmName']} ({a['StateValue']})"
        for a in resp.get("MetricAlarms", [])
    ]
    return {"answer": _fmt(alarms, "CloudWatch alarms"), "data": alarms}


def _list_cfn_stacks(region: str) -> dict[str, Any]:
    client = boto3.client("cloudformation", region_name=region)
    resp = client.describe_stacks()
    stacks = [
        f"{s['StackName']} ({s['StackStatus']})"
        for s in resp.get("Stacks", [])
    ]
    return {"answer": _fmt(stacks, "CloudFormation stacks"), "data": stacks}


def _list_dynamodb_tables(region: str) -> dict[str, Any]:
    client = boto3.client("dynamodb", region_name=region)
    resp = client.list_tables()
    tables = resp.get("TableNames", [])
    return {"answer": _fmt(tables, "DynamoDB tables"), "data": tables}


def _list_secrets(region: str) -> dict[str, Any]:
    client = boto3.client("secretsmanager", region_name=region)
    resp = client.list_secrets()
    secrets = [s["Name"] for s in resp.get("SecretList", [])]
    return {"answer": _fmt(secrets, "Secrets Manager secrets"), "data": secrets}


def _list_codebuild_projects(region: str) -> dict[str, Any]:
    client = boto3.client("codebuild", region_name=region)
    resp = client.list_projects()
    projects = resp.get("projects", [])
    return {"answer": _fmt(projects, "CodeBuild projects"), "data": projects}


def _list_sns_topics(region: str) -> dict[str, Any]:
    client = boto3.client("sns", region_name=region)
    resp = client.list_topics()
    topics = [t["TopicArn"].split(":")[-1] for t in resp.get("Topics", [])]
    return {"answer": _fmt(topics, "SNS topics"), "data": topics}


def _list_sqs_queues(region: str) -> dict[str, Any]:
    client = boto3.client("sqs", region_name=region)
    resp = client.list_queues()
    urls = resp.get("QueueUrls", [])
    queues = [u.split("/")[-1] for u in urls]
    return {"answer": _fmt(queues, "SQS queues"), "data": queues}


def _account_summary() -> dict[str, Any]:
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        account_id = identity.get("Account", "unknown")
        arn = identity.get("Arn", "unknown")
        answer = (
            f"AWS Account: {account_id}\n"
            f"Caller ARN: {arn}\n"
            "Ask me about S3 buckets, Lambda functions, EC2 instances, VPCs, RDS, ECS, IAM roles, "
            "CloudWatch alarms, DynamoDB, Secrets Manager, CodeBuild, SNS, or SQS."
        )
        return {"answer": answer, "data": {"account_id": account_id, "caller_arn": arn}}
    except Exception as exc:
        return {"answer": f"Could not retrieve account info: {exc}", "data": {}}
