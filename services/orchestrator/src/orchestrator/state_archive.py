from __future__ import annotations

import json
from typing import Any

import boto3

from orchestrator.models import DeploymentSession


class S3ProjectStateArchive:
    def __init__(self, bucket: str, region: str) -> None:
        self.bucket = bucket
        self.s3 = boto3.client("s3", region_name=region)

    def persist(self, session: DeploymentSession) -> dict[str, Any]:
        project_name = session.spec.name if session.spec else session.id
        prefix = f"{project_name}/{session.id}"
        payload = session.model_dump(mode="json")

        self._put_json(f"{prefix}/state.json", payload)
        self._put_json(f"{prefix}/logs/events.json", payload["events"])
        self._put_json(
            f"{prefix}/summary.json",
            {
                "project": project_name,
                "session_id": session.id,
                "status": session.status,
                "repository_url": session.repository_url,
                "resources": session.resources,
            },
        )
        return {
            "bucket": self.bucket,
            "prefix": prefix,
            "state_uri": f"s3://{self.bucket}/{prefix}/state.json",
            "logs_uri": f"s3://{self.bucket}/{prefix}/logs/events.json",
        }

    def put_text_artifact(
        self,
        session: DeploymentSession,
        filename: str,
        content: str,
        content_type: str = "text/plain",
    ) -> dict[str, Any]:
        project_name = session.spec.name if session.spec else session.id
        prefix = f"{project_name}/{session.id}/artifacts"
        key = f"{prefix}/{filename}"
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )
        download_url = self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=900,
        )
        return {
            "filename": filename,
            "s3_uri": f"s3://{self.bucket}/{key}",
            "download_url": download_url,
            "expires_in_seconds": 900,
        }

    def _put_json(self, key: str, payload: Any) -> None:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(payload, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
