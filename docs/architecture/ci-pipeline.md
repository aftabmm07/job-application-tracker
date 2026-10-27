# CI pipeline — implemented GitHub Actions workflow

This diagram reflects [backend-ci.yml](../../.github/workflows/backend-ci.yml).
It is separate from the **unprovisioned** Compose stack.

```mermaid
flowchart LR
    trigger["Pull request or push to main"] --> checkout["Checkout source"]
    checkout --> python["Set up Python 3.14.4"]
    python --> install["Install hash-locked wheels<br/>pip check"]
    install --> pytest["Run backend pytest suite"]
    pytest --> migration["Alembic upgrade head and metadata check<br/>fresh CI database"]
    migration --> ownership["PostgreSQL ownership tests<br/>explicitly enabled in CI"]
    postgres[("PostgreSQL 17<br/>GitHub Actions service")]
    postgres --> migration
    postgres --> ownership
```

The workflow has no Docker image build, registry push, or deployment stage.
The current workflow explicitly enables and runs the PostgreSQL ownership tests.
Its `alembic upgrade head` on the fresh CI database differs from the isolated
stack's separately reviewed migration target.
The separate receipt-bound PostgreSQL harness is not enabled by this workflow.
