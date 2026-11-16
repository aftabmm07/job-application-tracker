# Docker Deployment View — isolated local stack

This shows the resources defined by `compose.stack.yaml` for an approved
`jobtracker-stack-<run-id>` project. It is a local development deployment design,
not a public deployment or evidence of current availability. A
[recorded manual run](../container-stack.md) on 2026-10-29 was stopped afterward
with its database volume preserved; current runtime state has not been checked.

```mermaid
flowchart LR
    browser["Host browser"]
    operator["Local mailbox viewer"]
    subgraph project["One isolated Compose project — approved run ID"]
        subgraph edge["edge bridge network"]
            frontend["frontend<br/>Nginx :8080<br/>Vite-built React"]
            backend["backend<br/>FastAPI :8000"]
        end
        subgraph private["private bridge network — internal"]
            db[("db<br/>PostgreSQL 17 :5432")]
        end
        subgraph mail["mail bridge network — opt-in capture"]
            capture["capture<br/>Mailpit SMTP :1025<br/>UI :8025"]
        end
        volume[("Project-scoped persistent<br/>stack_pgdata volume")]
        frontend -->|"HTTP API on edge"| backend
        backend -->|"SQL/TCP on private"| db
        backend -.->|"SMTP on mail; opt-in"| capture
        db --- volume
    end
    browser -->|"HTTP 127.0.0.1:18080"| frontend
    operator -.->|"HTTP 127.0.0.1:18025; opt-in"| capture

    classDef actor fill:#e8f1ff,stroke:#4466aa,color:#172b4d
    classDef app fill:#e8f6ed,stroke:#348054,color:#153723
    classDef data fill:#eee9fb,stroke:#69529d,color:#35265c
    classDef optional fill:#fff4df,stroke:#9b7226,color:#4d3916
    class browser,operator actor
    class frontend,backend app
    class db,volume data
    class capture optional
```

The backend also joins `private` and `mail`; the frontend joins only `edge`,
PostgreSQL joins only `private`, and Mailpit joins only `mail`. Only the frontend
and optional mailbox UI publish host ports, both on loopback. Mailpit uses
disposable tmpfs mail storage; PostgreSQL uses the project-scoped persistent
volume. The private network is `internal: true`; the mail network is not.

Compose waits for a healthy database before backend startup and a healthy
backend before frontend startup. Health checks do not apply migrations. The
capture service requires an explicit profile, reviewed image digest, and
development-only delivery setting; normal production SMTP is not part of this
stack. Project naming alone does not establish resource identity or safety.
Follow the runbook's separate preflight and approval gates.
