# wallex-agent

AI trading agent for the StarCoach Agentic AI hackathon, Wallex challenge.

**Claude sets the course, the models pick the moments, code holds the brakes.**
«Claude مسیر را تعیین می‌کند، مدل‌ها لحظه را انتخاب می‌کنند، کد ترمز می‌گیرد»

Status: skeleton (step C0). Design: `../docs/wallex-build-spec.md` (v2). The run guide, `STRATEGY.md` and `RUNBOOK.md` are filled in as the build progresses.

## Machines
- **Server** (Ubuntu 26.04, Iran): engine, risk gate, Wallex orders. Python 3.14.
- **Laptop** (AI box): Claude manager calls + self-hosted Clef judge. Python 3.12, `uv sync --extra ai-box`.

## Secrets
No key is ever stored in this repo. The Wallex key comes from `WALLEX_KEY_FILE` (server env file); the Anthropic key stays on the laptop (`ANTHROPIC_API_KEY` in a laptop-only env file).
