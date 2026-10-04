# Security Policy

## Supported versions

Only the latest tagged release is supported with security fixes.

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use GitHub's private
vulnerability reporting: go to the repository's **Security** tab and choose
**Report a vulnerability**. We'll acknowledge the report and work on a fix
privately before disclosing it.

## Threat model

marv is a local-first coding agent. It runs entirely on your machine and does not
send data to a cloud service by itself. Be aware of the following when using it:

- **Tools run with your permissions.** The `bash`, `write`, and `edit` tools are
  not sandboxed. marv can do anything your user account can do, in any directory
  it can reach — including outside the project you launched it in.
- **The model decides what to run.** Tool calls come from the model's output. A
  prompt-injected file or web page could steer a model into a destructive
  command. Treat agent output as untrusted.
- **Approval mode is a guardrail, not a sandbox.** Setting `approval_mode` to
  `destructive` (or `all`) prompts before risky tool calls, but its destructive
  classification is a heuristic and can miss things. Do not rely on it for
  hard isolation.
- **Extensions are arbitrary code.** Any extension you load runs in-process with
  full access. Only load extensions you trust.

For real isolation, run marv inside a container or a restricted user account.
