import "./globals.css";

export const metadata = {
  title: "AgentCore Multi-Agent Deployer",
  description: "Create, deploy, remediate, and govern Terraform infrastructure with AgentCore agents.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
