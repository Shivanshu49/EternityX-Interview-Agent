# Frontend

Single-page chat UI for the interview agent. No build step, no dependencies, no
CDN. One `index.html` served by FastAPI at `/`, so the deployed URL is the demo.

## Running

```bash
uvicorn app.main:app --reload
# open http://127.0.0.1:8000/
```

The page needs a running API on the same origin; it posts to `/api/interview`
relative to itself, so no CORS setup is involved.

## Flow

| Step | Request | Response |
| --- | --- | --- |
| Start | `{sessionId, candidate}` | `{reply, done: false}` |
| Turn | `{sessionId, message}` | `{reply, done: false}` |
| Final turn | `{sessionId, message}` | `{reply, done: true, feedback: {...}}` |

`sessionId` is generated per interview in the browser. The candidate payload is
editable in the page, and a sample is pre-filled so the demo runs with one click.
Non-2xx responses surface the API's `detail` string inline rather than failing
silently.
