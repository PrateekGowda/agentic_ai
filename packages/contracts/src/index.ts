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
  | "blocked";

export type AgentName = "requirements" | "provisioner" | "deployer" | "compliance";
export type EventSeverity = "info" | "warning" | "error" | "success";

export interface DeploymentSpec {
  name: string;
  description: string;
  cloud: "aws";
  region: string;
  environment: Environment;
  workloadType: "s3-lambda-api" | "vpc-baseline";
  owner: string;
  costCenter: string;
  complianceProfile: "baseline" | "regulated";
  githubVisibility: "private" | "internal" | "public";
  tags: Record<string, string>;
  standardsSource?: string;
}

export interface CustomizationQuestion {
  id: string;
  label: string;
  helpText?: string;
  defaultValue?: string;
  required: boolean;
}

export interface DeploymentEvent {
  id: string;
  sessionId: string;
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
  repositoryUrl?: string;
  architectureDocUrl?: string;
  complianceReportUrl?: string;
  customizationQuestions: CustomizationQuestion[];
  findings: ComplianceFinding[];
  events: DeploymentEvent[];
  github_token_configured?: boolean;
  createdAt: string;
  updatedAt: string;
}
