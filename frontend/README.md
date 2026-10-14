# Job Application Tracker Frontend

React dashboard with an Add Application form built with Vite and JavaScript. It fetches the existing
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
network throttling to check the loading state. The dashboard fetches applications with GET and creates them with POST.
Editing and deletion are not implemented.

`npm run preview` serves the production build but does not use the development
proxy. A deployment must route `/applications` to the backend on the same origin.

## Adding an application

Select **+ Add Application**. Company name and job title are required, trimmed,
and limited to 150 and 200 characters respectively. Location is optional and
limited to 150 characters. Status defaults to `saved`; the other choices are
`applied`, `interview`, `offer`, and `rejected`. Blank location, application date,
and job description are sent as JSON `null`. Dates are sent as `YYYY-MM-DD`.

Save to POST `/applications`. On success the dialog closes and GET `/applications`
refreshes the table and counts. Cancel or Escape closes without saving; closing
and repeat submissions are disabled while saving. Validation and API errors leave
the entered values available for correction.

Test with your own application data and verify the refreshed row and counts.
Also check blank/whitespace-only required fields, optional fields left blank,
keyboard navigation and Escape, and a narrow viewport. Stop FastAPI before saving
to check error feedback, then restart it and retry. Use browser network throttling
to observe the disabled controls while saving. FastAPI 422 detail arrays are shown
as readable field messages, and non-JSON server errors use an HTTP status fallback.
