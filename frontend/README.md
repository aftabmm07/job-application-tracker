# Job Application Tracker Frontend

Read-only React dashboard built with Vite and JavaScript. It fetches the existing
`GET /applications` endpoint and derives all summary counts from the response.

## Local development

Use Node.js 24 LTS and npm. Start the existing PostgreSQL database and FastAPI
server using the instructions in `../docs/setup.md`. FastAPI must listen at
`http://127.0.0.1:8000`.

From `frontend/`, run:

```bash
npm install
npm run dev
```

Open the local URL printed by Vite. The development server proxies
`/applications` to FastAPI, so no backend CORS changes are required.

## Checks

```bash
npm run build
npm run lint
```

Check the dashboard with existing database records: compare its rows and summary
counts against `http://127.0.0.1:8000/applications`. Nullable locations and dates
show “Not provided”. At a narrow viewport the summary cards reflow and the table
scrolls horizontally (including via keyboard focus).

An empty database shows zero counts and an empty message. Stop FastAPI and reload
to check the error state, then restart FastAPI and select **Try again**. Use browser
network throttling to check the loading state. The dashboard only sends GET
requests; it cannot create, update, or delete records.

`npm run preview` serves the production build but does not use the development
proxy. A deployment must route `/applications` to the backend on the same origin.
