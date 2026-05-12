export type Environment = "dev" | "test" | "stage" | "prod";
export type DeploymentStatus =
  | "requirements"
  | "customizing"
  | "repo_created"
  | "policy_check"
  | "awaiting_approval"
  | "deploying"
  | "remediating"
  | "succeeded"
  | "failed"
  | "blocked"
  | "destroyed";

export type AgentName = "requirements" | "provisioner" | "deployer" | "compliance" | "destroyer";
export type EventSeverity = "info" | "warning" | "error" | "success";

export interface DeploymentSpec {
  name: string;
  description: string;
  cloud: "aws";
  region: string;
  environment: Environment;
  workload_type: "s3-lambda-api" | "vpc-baseline" | "ec2-httpd" | "s3-bucket";
  owner: string;
  cost_center: string;
  compliance_profile: "baseline" | "regulated";
  github_visibility: "private" | "internal" | "public";
  tags: Record<string, string>;
  standards_source?: string;
}

export interface CustomizationQuestion {
  id: string;
  label: string;
  help_text?: string;
  default_value?: string;
  required: boolean;
}

export interface DeploymentEvent {
  id: string;
  session_id: string;
  timestamp: string;
  agent: AgentName;
  severity: EventSeverity;
  status: DeploymentStatus;
  message: string;
  details?: Record<string, unknown>;
}

export interface ComplianceFinding {
  id: string;
  tool: "opa" | "checkov" | "aws";
  severity: "low" | "medium" | "high" | "critical";
  title: string;
  resource?: string;
  remediation: string;
  blocking: boolean;
}

export interface DeploymentSession {
  id: string;
  status: DeploymentStatus;
  spec?: DeploymentSpec;
  repository_url?: string;
  architecture_doc_url?: string;
  compliance_report_url?: string;
  customization_questions: CustomizationQuestion[];
  findings: ComplianceFinding[];
  events: DeploymentEvent[];
  github_token_configured?: boolean;
  resources?: Record<string, any>;
  created_at: string;
  updated_at: string;
}
