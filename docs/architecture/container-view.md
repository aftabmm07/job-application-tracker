# C4 Container View — Job Application Tracker

These are logical C4 application containers and data stores, mapped to services
in `compose.stack.yaml`. A C4 container is not inherently a Docker container.
Vite builds React assets into the frontend image; Nginx serves them at runtime.
The [runbook](../container-stack.md) records one manual local run and shutdown;
this view makes no claim about current availability.

```mermaid
flowchart LR
    browser["Job seeker's browser"]
    operator["Local demo operator"]
    subgraph system["Job Application Tracker — isolated development stack"]
        frontend["Frontend<br/>Nginx serves Vite-built React assets<br/>Same-origin API reverse proxy"]
        backend["Backend<br/>FastAPI: auth, verification, profile,<br/>owner-scoped CRUD"]
        db[("PostgreSQL 17<br/>Users, sessions and applications")]
        capture["Mailpit — optional<br/>Development-only email capture"]
    end
    smtp["Configured SMTP server<br/>Code capability; not used by this stack"]

    browser -->|"HTTP, localhost:18080"| frontend
    frontend -->|"HTTP API: /auth and /applications<br/>edge network"| backend
    backend -->|"SQL/TCP, db:5432<br/>private network"| db
    backend -.->|"SMTP, capture:1025<br/>mail network; opt-in"| capture
    operator -.->|"HTTP mailbox UI<br/>localhost:18025; opt-in"| capture
    backend -.->|"Authenticated STARTTLS<br/>other supported configurations only"| smtp

    classDef actor fill:#e8f1ff,stroke:#4466aa,color:#172b4d
    classDef app fill:#e8f6ed,stroke:#348054,color:#153723
    classDef data fill:#eee9fb,stroke:#69529d,color:#35265c
    classDef optional fill:#fff4df,stroke:#9b7226,color:#4d3916
    class browser,operator actor
    class frontend,backend app
    class db data
    class capture,smtp optional
```

Only Nginx and the optional Mailpit UI publish loopback host ports. FastAPI
enforces session, trusted-origin, CSRF, and owner checks; CSRF applies to
protected mutations, not every browser request. Verification links are delivered
only when a sender is explicitly configured. Migration `0004` defines the
outbox table, but no outbox worker or production path uses it. Migrations require
separate approval and are not run at API startup.
