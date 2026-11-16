# CI Pipeline — implemented GitHub Actions

The [Backend CI](../../.github/workflows/backend-ci.yml) and
[Frontend CI](../../.github/workflows/frontend-ci.yml) workflows run on pull
requests targeting `main` and pushes to `main`. They validate source; they do
not deploy the application or publish release images.

```mermaid
flowchart LR
    trigger["Pull request to main<br/>or push to main"]
    subgraph backend["Backend CI — ubuntu-24.04"]
        py["Python 3.14.4"] --> wheels["Install hash-locked wheels<br/>pip check"]
        wheels --> tests["Backend pytest suite"]
        tests --> migration["Alembic upgrade head<br/>and metadata check"]
        migration --> ownership["PostgreSQL ownership tests<br/>explicitly enabled"]
        postgres[("Fresh PostgreSQL 17<br/>CI service")]
        postgres --> migration
        postgres --> ownership
    end
    subgraph frontend["Frontend CI — Ubuntu runner"]
        node["Node.js 24"] --> npm["npm ci<br/>frontend lockfile cache"]
        npm --> unit["npm test"]
        unit --> lint["npm run lint"]
        lint --> build["npm run build"]
    end
    trigger --> py
    trigger --> node

    classDef entry fill:#e8f1ff,stroke:#4466aa,color:#172b4d
    classDef check fill:#e8f6ed,stroke:#348054,color:#153723
    classDef data fill:#eee9fb,stroke:#69529d,color:#35265c
    class trigger entry
    class py,wheels,tests,migration,ownership,node,npm,unit,lint,build check
    class postgres data
```

Backend CI's `alembic upgrade head` targets its fresh CI database. The isolated
development stack instead requires a separately reviewed migration target and
operator approval. PostgreSQL ownership tests are explicitly enabled and run in
Backend CI. Playwright browser E2E tests do **not** run in GitHub Actions; neither
workflow contains a deployment or image-publishing stage.
