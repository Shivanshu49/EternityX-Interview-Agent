# EternityX Interview Agent - Web UI

Next.js + Tailwind frontend for the FastAPI interview backend. Single page:
pick or edit a candidate, run the adaptive interview, get the written report.

## Run locally

```bash
cd web
npm install
npm run dev          # http://localhost:3000
```

The app calls the backend directly from the browser. It defaults to
`http://localhost:8000` during local development. Production builds default to
`https://eternityx-interview-agent.onrender.com`; override either default with:

```bash
NEXT_PUBLIC_API_URL=https://your-backend.example.com npm run dev
```

or copy `.env.example` to `.env.local` and edit it.

> The backend must send CORS headers for the app's origin, because the browser
> calls it cross-origin (localhost:3000 -> localhost:8000, and Vercel -> backend).
> See "CORS" in the main project README / ask the backend team.

## Deploy to Vercel

1. Import the repo in Vercel and set **Root Directory** to `web/`.
2. Optionally add `NEXT_PUBLIC_API_URL` = a different deployed backend URL (no
   trailing slash). Without it, production uses the project Render API.
3. Deploy. `NEXT_PUBLIC_*` is baked at build time - redeploy after changing it.

## Notes

- Contract: `POST {API}/api/interview` with `{sessionId, candidate}` to start,
  `{sessionId, message}` per turn; final response has `done: true` plus a
  `feedback` object which renders as the report.
- The "Explain question choices" toggle adds `?explain=1` and shows the
  engine's day-selection reasoning under each question.
- Sample candidates in `lib/candidates.ts` are copied from `candidates.json`.
- Sessions are in-memory on the backend; restarting it invalidates the current
  interview (the UI surfaces this as "session not found" - start a new one).
