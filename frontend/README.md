# Job Application Tracker Frontend

React dashboard with add, edit, and confirmed deletion built with Vite and JavaScript. It fetches the existing
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
Applications can also be updated with PATCH and removed with a confirmed DELETE.

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

## Editing an application

1. Start PostgreSQL and FastAPI following `../docs/setup.md`, then run
   `npm run dev` from `frontend/` and open the printed URL.
2. Select **Edit** on an existing row. Check all six fields against that record,
   including its description and any missing optional values.
3. Change the company, title, location, status, date, and description, then select
   **Save changes**. In browser DevTools Network, verify PATCH `/applications/{id}`
   followed by GET `/applications`. Confirm success feedback, the updated row,
   and status counts; total applications should stay unchanged. Reload to verify
   persistence, and reopen Edit to inspect the saved description.
4. Clear optional location, date, and description and save. The PATCH sends JSON
   `null` for these fields. Reopen to confirm they are blank.
5. Try blank and whitespace-only required company/title values. Saving must be
   blocked. Verify the same 150/200/150 character limits as the add form.
6. Change a field, then Cancel or press Escape. Reopen and confirm the original
   data remains. Tab through the dialog and check focus returns to Edit on cancel;
   after a save and list refresh, focus falls back to Add Application.
7. Check a narrow viewport and horizontal table scrolling. Throttle requests to
   verify saving feedback and disabled controls prevent repeat submissions.
8. Stop FastAPI before saving to check network errors. For HTTP 404, 422, and 503,
   use browser request interception/local response overrides on the PATCH request
   with the corresponding status and JSON bodies, for example
   `{"detail":"Job application not found"}`,
   `{"detail":[{"loc":["body","company_name"],"msg":"Invalid value"}]}`, or
   `{"detail":"Database operation failed"}`. Verify readable errors and preserved
   inputs, remove the override, and retry. Do not delete records to test 404.
9. Use **+ Add Application** to verify creation still sends POST and refreshes the
   dashboard.

Editing sends all six editable fields in a single PATCH payload. Required company
and title values are trimmed strings, status is a backend enum value, and optional
fields contain a value or explicit `null`. IDs and creation timestamps are never
included. Both modes use the same form, validation, styles, and status constants.

## Deleting an application

1. Start PostgreSQL and FastAPI using `../docs/setup.md`, then run `npm run dev`
   from `frontend/` and open the printed URL. Choose a disposable application.
2. Open DevTools Network and select its red **Delete** action. Check the company
   and job title in the dialog. Opening it must not send DELETE; Cancel has focus.
3. Cancel, reopen, and press Escape. Confirm no DELETE was sent, the row and counts
   remain unchanged, and focus returns to the row's Delete button. Reopen and click
   outside the dialog: it must stay open without sending a request.
4. Confirm with **Delete Application**. Verify one DELETE `/applications/{id}` with
   no request body, an empty 204 response, then GET `/applications`. Check success
   feedback, the removed row, total reduced by one, and the corresponding status
   count reduced by one. Focus returns to Add Application. Reload to verify removal.
5. Test on a narrow viewport and using Tab/Shift+Tab. Throttle requests before
   confirming to check the deleting message and disabled buttons; repeated clicks
   must not send duplicate requests. Cancel and Escape are disabled while deleting.
6. Stop FastAPI before confirming to test network feedback. The dialog must stay
   open and allow Cancel. Restart FastAPI to retry. For 404/503, intercept the DELETE
   request with a local testing tool and return the relevant status (for example
   `{"detail":"Job application not found"}` or
   `{"detail":"Database operation failed"}`). Check the readable error, retained
   dialog, and available Cancel/retry controls; remove interception afterward.
7. Delete the last disposable record to check zero counts and the empty state.
   Verify Add Application and Edit still work normally.

The confirmation uses a native modal dialog with no outside-click dismissal.
Only the explicit confirmation button starts DELETE. Successful responses are not
parsed as JSON because the backend returns 204 No Content. Counts always come from
GET after the deletion; no optimistic statistics are inserted.

## Searching and filtering applications

Search matches company name or job title, case-insensitively, ignoring leading and
trailing query whitespace. Status and search apply together. Filtering uses the
already fetched list and sends no API requests. Summary cards always count all
applications; the panel shows the matching count out of the full total. Filters
remain selected after creating, editing, deleting, or retrying a fetch, and are
applied to the refreshed records. Reloading the page resets filters.

1. Start PostgreSQL and FastAPI using `../docs/setup.md`; run `npm run dev` from
   `frontend/` and open the printed URL.
2. Search for part of an existing company name, then part of a job title. Check
   immediate updates, mixed-case queries, and leading/trailing spaces. A query
   containing only spaces should match all records when All statuses is selected.
3. Select each status and combine it with a search. Verify every row matches both
   conditions, the “N of total applications” count is correct, and summary cards
   stay unchanged. Check DevTools Network: filtering must not send requests.
4. Enter a query with no matches: expect “No matching applications”. Select
   **Clear filters**: expect an empty search, All statuses, and all records.
   An entirely empty database should instead show “No applications yet”.
5. With filters active, create a matching application, edit a matching row so it
   no longer matches, and delete a disposable matching row. Verify retained
   filters, refreshed matching counts, and global summaries after each operation.
6. Tab through search, status, and Clear filters; check a narrow viewport. Confirm
   loading, API failure/retry, and the existing creation/edit/deletion dialogs
   continue to work.
