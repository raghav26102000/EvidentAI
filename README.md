<div align="center">

# EvidentAI

**A multi-agent AI system for data analytics that checks its own work before showing you the answer.**

Upload a dataset. A coordinated team of specialized agents profiles it, runs real statistical analysis, writes up ranked insights, and independently verifies every claim against the raw numbers before you ever see it.

</div>

---

## The problem with most "AI data analyst" tools

Most tools in this space do one thing: an LLM reads your data and writes a paragraph describing it. That's summarization dressed up as analysis, and it has no mechanism to catch its own mistakes. If the model overstates a correlation or misreads a trend, nothing stops it from confidently telling you something false.

EvidentAI is built around a different idea: **separate the agent that finds things from the agent that checks them.** Statistical computation runs as real, executed code, not an LLM's best guess. Every insight is reviewed by an independent critic agent, in its own session, against the actual numbers, before it reaches the user. If a claim isn't supported by the data, the critic rejects it with specific feedback and the system tries again.

## How it works

```
 Upload
   │
   ▼
 Data Profiling  (deterministic, no LLM — column types, nulls, cardinality, stats)
   │
   ▼
 Statistical Analysis Agent
   │  → selects relevant tests (correlation, outlier detection, etc.)
   │  → executes real Python code in a sandboxed environment
   ▼
 Insight Agent  (LLM)
   │  → writes ranked, plain-language findings
   ▼
 Critic Agent  (LLM, independent session)
   │  → checks test selection and every claim against the raw stat output
   │
   ├── Approved ──────────────► shown to user
   │
   └── Rejected ──► specific feedback ──► back to Insight Agent (up to 2 retries)
                                              │
                                     still rejected after 2 tries
                                              │
                                              ▼
                                  shown as "low confidence," with the
                                  critic's objection attached — never hidden
```

Every agent has one job. The critic never speaks to the user directly, only to the agent it's reviewing, and every decision is logged with its full reasoning, visible in the agent trail view.

## What you can see in the demo

- **A dashboard** with a ranked insights panel, an interactive correlation heatmap, and an outlier visualization, all rendered from real computed statistics
- **An agent trail view** showing the exact sequence of agent activity for any analysis, including a real example where the insight agent overstated a correlation, the critic caught it by citing the actual value against the claimed one, and a corrected version was produced and approved on the next attempt
- **Live processing view** showing which agent is currently working as an analysis runs

To see the full reject-and-retry story, run an analysis from the dashboard and open its agent trail. It's the clearest proof point in the project.

## Architecture and stack

| Layer | Choice |
|---|---|
| Frontend | React + Vite, TypeScript, Tailwind CSS, Recharts |
| Backend | FastAPI (Python) |
| Database | PostgreSQL with row-level security enforcing tenant isolation at the database layer |
| Auth | JWT, Argon2 password hashing, rotating refresh tokens |
| File storage | Encrypted object storage, per-tenant keys |
| Malware scanning | ClamAV on every upload, before any processing |
| LLM layer | Provider-agnostic client; insight and critic agents run on independently configurable models |
| Code execution | Sandboxed via seccomp-bpf syscall filtering, dedicated non-root user, strict resource limits |

## Security model

This system executes LLM-generated code against user data, a real attack surface. The design treats it that way:

- **Sandboxed execution**: generated statistical code runs as a dedicated unprivileged user under a seccomp syscall whitelist. Any attempt to open a network socket or access the filesystem is killed at the kernel level, not caught in application code. Verified with adversarial tests: attempted network calls, filesystem reads outside the sandbox, and runaway loops are all blocked, while legitimate statistical computation completes correctly and returns mathematically verified results.
- **Tenant isolation**: enforced with PostgreSQL row-level security, not just query filters in application code. A tenant's data is invisible to every other tenant at the database layer itself, verified directly: cross-tenant reads return nothing, spoofed writes into another tenant's rows are rejected outright, and queries with no tenant context fail closed rather than returning data.
- **Auth**: short-lived JWT access tokens, rotating refresh tokens, Argon2 hashing.
- **Upload pipeline**: strict file type and size validation plus malware scanning ahead of any parsing.

## Roadmap

- **Scheduled monitoring**: re-run analysis on a data source on an interval and alert on material changes, a metric drifting outside its normal range, a correlation breaking down, a new outlier pattern emerging. Scoped in the original architecture, planned as the next phase.
- **Auto-recovery on statistical test rejection**: currently the critic can reject a statistical test choice as well as an insight claim; test-choice rejections are logged for review, while insight rejections trigger an automatic retry loop. Extending automatic retry to test selection is planned.
- **Exact LLM token accounting**: current cost tracking uses character-based estimates; swapping in exact provider-reported token counts is a small, scoped change.

## Local setup

```bash
# backend
cd backend
pip install -r requirements.txt
# configure .env: POSTGRES_URL, POSTGRES_AUTH_URL, JWT keypair, MASTER_KEK_B64, EMERGENT_LLM_KEY
uvicorn server:app --reload

# frontend
cd frontend
yarn install
yarn dev
```

---

<div align="center">

Built by **Raghav Agarwal**
[GitHub](https://github.com/raghav26102000) · [LinkedIn](https://linkedin.com/in/raghav-agarwal26)

</div>
