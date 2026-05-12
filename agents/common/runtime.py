"""HTTP runtime entrypoint for Amazon Bedrock AgentCore Runtime."""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from compliance_agent import run_compliance_checks
from deployer_agent import run_deployment_step
from provisioner_agent import provision_repository_payload
from requirement_agent import handle_requirement_message

AGENTS = {
    "requirements": handle_requirement_message,
    "provisioner": provision_repository_payload,
    "deployer": run_deployment_step,
    "compliance": run_compliance_checks,
}


class AgentRuntimeHandler(BaseHTTPRequestHandler):
    def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/ping":
            self._write_json(404, {"status": "not_found"})
            return
        self._write_json(200, {"status": "Healthy", "time_of_last_update": int(time.time())})

    def do_POST(self) -> None:
        if self.path != "/invocations":
            self._write_json(404, {"status": "not_found"})
            return

        agent_name = os.getenv("AGENT_NAME", "requirements")
        agent = AGENTS.get(agent_name)
        if not agent:
            self._write_json(400, {"message": f"Unknown AGENT_NAME: {agent_name}", "data": {}})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
        request = json.loads(raw_body or "{}")
        payload = request.get("input", request)
        self._write_json(200, agent(payload))


def main() -> None:
    HTTPServer(("0.0.0.0", 8080), AgentRuntimeHandler).serve_forever()


if __name__ == "__main__":
    main()
