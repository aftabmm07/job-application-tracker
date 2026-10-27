# Docker deployment — proposed, unprovisioned stack

The diagram shows intended resources in one new
`jobtracker-stack-<approved-run-id>` Compose project. It does **not** show a
running deployment or any protected original/demo resources.

```mermaid
flowchart LR
    browser["Host browser"]
    subgraph project["Isolated Compose project — unprovisioned"]
        frontend["frontend<br/>Nginx :8080"]
        edge(("edge network"))
        backend["backend<br/>FastAPI :8000"]
        private(("private network<br/>internal"))
        db["db<br/>PostgreSQL 17 :5432"]
        volume[("Project-scoped<br/>stack_pgdata volume")]
        frontend -->|"API requests"| edge
        edge --> backend
        backend -->|"SQL"| private
        private --> db
        db --- volume
    end
    browser -->|"127.0.0.1:18080 only"| frontend
```

PostgreSQL and FastAPI publish no host ports. The frontend joins only `edge`,
the database joins only `private`, and the backend joins both. Compose scopes
the volume and networks to the approved project name; naming alone does not
prove resource availability or Docker daemon identity. See the
[operator runbook](../container-stack.md) for separate preflight and approval
steps. Compose waits for a healthy database before starting the backend and a
healthy backend before starting the frontend; these checks do not apply
migrations. No Mailpit or real SMTP service is part of this proposal.
