# C4 System Context — Job Application Tracker

This C4-style context view shows the system, its primary user, and external
interactions. It does not describe source files or a currently running service.
The [isolated stack runbook](../container-stack.md) records one manual local run
on 2026-10-29 followed by shutdown; current availability has not been checked.

```mermaid
flowchart LR
    seeker["Job seeker"]
    operator["Local demo operator"]
    subgraph system["Job Application Tracker system"]
        tracker["Private job applications, analytics, profile<br/>Registration, email verification and sign-in"]
    end
    capture["Mailpit<br/>Optional local development capture"]
    smtp["Configured SMTP server<br/>Supported outside this stack; no provider configured here"]
    seeker -->|"Uses in a browser over HTTP in the local stack"| tracker
    tracker -.->|"Verification email via local SMTP; opt-in"| capture
    operator -.->|"Views disposable captured mail; opt-in"| capture
    tracker -.->|"Authenticated STARTTLS in other configurations only"| smtp

    classDef actor fill:#e8f1ff,stroke:#4466aa,color:#172b4d
    classDef systemNode fill:#e8f6ed,stroke:#348054,color:#153723
    classDef optional fill:#fff4df,stroke:#9b7226,color:#4d3916
    class seeker,operator actor
    class tracker systemNode
    class capture,smtp optional
```

The isolated Compose stack defaults to no email delivery. Its optional,
development-only Mailpit capture is shown in the container and deployment views;
it is not a production email provider. Normal SMTP is supported by application
code but forbidden in this stack mode. The transactional email outbox remains
disabled.
