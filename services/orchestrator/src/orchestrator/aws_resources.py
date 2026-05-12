from __future__ import annotations

import time
from typing import Any

import boto3


class Ec2HttpdManager:
    def __init__(self, region: str) -> None:
        self.ec2 = boto3.client("ec2", region_name=region)
        self.ssm = boto3.client("ssm", region_name=region)

    def create(self, project_id: str, project_name: str, create_ssh_key: bool = False) -> dict[str, Any]:
        vpc_id = self.ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"][0][
            "VpcId"
        ]
        subnet_id = self.ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "default-for-az", "Values": ["true"]},
            ]
        )["Subnets"][0]["SubnetId"]
        image_id = self.ssm.get_parameter(
            Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
        )["Parameter"]["Value"]
        key_name = None
        private_key_pem = None
        if create_ssh_key:
            key_name = f"agentcore-{project_id[:8]}-httpd"
            key_pair = self.ec2.create_key_pair(KeyName=key_name, KeyType="rsa")
            private_key_pem = key_pair["KeyMaterial"]

        group_name = f"agentcore-{project_id[:8]}-httpd"
        security_group = self.ec2.create_security_group(
            GroupName=group_name,
            Description=f"HTTP access for {project_name}",
            VpcId=vpc_id,
            TagSpecifications=[
                {
                    "ResourceType": "security-group",
                    "Tags": self._tags(project_id, project_name),
                }
            ],
        )
        security_group_id = security_group["GroupId"]
        self.ec2.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 80,
                    "ToPort": 80,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTP test access"}],
                }
            ],
        )
        if create_ssh_key:
            self.ec2.authorize_security_group_ingress(
                GroupId=security_group_id,
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 22,
                        "ToPort": 22,
                        "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "Temporary SSH access for requested PEM"}],
                    }
                ],
            )

        user_data = """#!/bin/bash
dnf install -y httpd
cat >/var/www/html/index.html <<'EOF'
<html><body><h1>AgentCore EC2 httpd test succeeded</h1></body></html>
EOF
systemctl enable --now httpd
"""
        response = self.ec2.run_instances(
            ImageId=image_id,
            InstanceType="t3.micro",
            MinCount=1,
            MaxCount=1,
            SubnetId=subnet_id,
            SecurityGroupIds=[security_group_id],
            UserData=user_data,
            **({"KeyName": key_name} if key_name else {}),
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": self._tags(project_id, project_name),
                }
            ],
        )
        instance_id = response["Instances"][0]["InstanceId"]
        self.ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

        public_ip = None
        for _ in range(12):
            instance = self.ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0][
                "Instances"
            ][0]
            public_ip = instance.get("PublicIpAddress")
            if public_ip:
                break
            time.sleep(5)

        return {
            "instance_id": instance_id,
            "security_group_id": security_group_id,
            "public_ip": public_ip,
            "url": f"http://{public_ip}" if public_ip else None,
            "access_method": "ssh_key_pair" if key_name else "ssm_session_manager",
            "key_name": key_name,
            "private_key_pem": private_key_pem,
        }

    def destroy(self, resources: dict[str, Any]) -> dict[str, Any]:
        instance_id = resources.get("instance_id")
        security_group_id = resources.get("security_group_id")
        destroyed: dict[str, Any] = {"instance_id": instance_id, "security_group_id": security_group_id}

        if instance_id:
            self.ec2.terminate_instances(InstanceIds=[instance_id])
            self.ec2.get_waiter("instance_terminated").wait(InstanceIds=[instance_id])
            destroyed["instance_terminated"] = True

        if security_group_id:
            try:
                self.ec2.delete_security_group(GroupId=security_group_id)
                destroyed["security_group_deleted"] = True
            except self.ec2.exceptions.ClientError as exc:
                destroyed["security_group_deleted"] = False
                destroyed["security_group_error"] = str(exc)
        key_name = resources.get("key_name")
        if key_name:
            try:
                self.ec2.delete_key_pair(KeyName=key_name)
                destroyed["key_pair_deleted"] = True
            except self.ec2.exceptions.ClientError as exc:
                destroyed["key_pair_deleted"] = False
                destroyed["key_pair_error"] = str(exc)

        return destroyed

    def _tags(self, project_id: str, project_name: str) -> list[dict[str, str]]:
        return [
            {"Key": "ManagedBy", "Value": "agentcore-multi-agent-deployer"},
            {"Key": "ProjectId", "Value": project_id},
            {"Key": "Name", "Value": project_name},
        ]


class S3BucketManager:
    def __init__(self, region: str) -> None:
        self.region = region
        self.s3 = boto3.client("s3", region_name=region)

    def create(self, project_id: str, project_name: str) -> dict[str, Any]:
        bucket_name = self._bucket_name(project_id, project_name)
        params: dict[str, Any] = {"Bucket": bucket_name}
        if self.region != "us-east-1":
            params["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        self.s3.create_bucket(**params)
        self.s3.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        self.s3.put_bucket_versioning(Bucket=bucket_name, VersioningConfiguration={"Status": "Enabled"})
        self.s3.put_bucket_encryption(
            Bucket=bucket_name,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256",
                        }
                    }
                ]
            },
        )
        self.s3.put_bucket_tagging(
            Bucket=bucket_name,
            Tagging={
                "TagSet": [
                    {"Key": "ManagedBy", "Value": "agentcore-multi-agent-deployer"},
                    {"Key": "ProjectId", "Value": project_id},
                    {"Key": "Name", "Value": project_name},
                ]
            },
        )
        return {"bucket_name": bucket_name, "bucket_uri": f"s3://{bucket_name}", "region": self.region}

    def destroy(self, resources: dict[str, Any]) -> dict[str, Any]:
        bucket_name = resources.get("bucket_name")
        if not bucket_name:
            return {"message": "No S3 bucket tracked for this project."}
        paginator = self.s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket_name):
            objects = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in page.get("Versions", []) + page.get("DeleteMarkers", [])
            ]
            if objects:
                self.s3.delete_objects(Bucket=bucket_name, Delete={"Objects": objects})
        self.s3.delete_bucket(Bucket=bucket_name)
        return {"bucket_name": bucket_name, "bucket_deleted": True}

    def _bucket_name(self, project_id: str, project_name: str) -> str:
        safe_name = "".join(char.lower() if char.isalnum() else "-" for char in project_name).strip("-")
        return f"{safe_name[:35]}-{project_id[:8]}-agentcore"
