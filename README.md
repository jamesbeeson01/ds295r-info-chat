# DS295R Info Chat

A simple, mobile-friendly AI chat that answers questions about one class —
and nothing else. Students open a link on their phone, ask "when are office
hours?", and get an answer sourced exclusively from a single markdown file.

Built with:

- **[LangChain v1 (Python)](https://docs.langchain.com/)** + **Gemini**
  (`gemini-3.1-flash-lite`) for answering questions
- **[agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui)**
  (Next.js) for the chat interface
- **FastAPI** serverless backend implementing the LangGraph server protocol
  the UI speaks
- **[uv](https://docs.astral.sh/uv/)** for Python package management
- **Vercel** for hosting — one project serves both the UI and the API

```
Browser (agent-chat-ui, Next.js)
   │  /api/threads, /api/threads/{id}/runs/stream (SSE), ...
   ▼
Vercel Python Function (api/index.py, FastAPI)
   │  system prompt = course_info.md
   ▼
Gemini via LangChain
```

## The only file you need to edit: `course_info.md`

[`course_info.md`](course_info.md) is the assistant's **entire knowledge
base**. It is injected as the system prompt on every request. Replace the
`TODO` placeholders with your course's schedule, grading policy, office
hours, contacts, etc. Commit and push — Vercel redeploys automatically and
the assistant immediately knows the new content. No retraining, no database.

## Deploy to Vercel

1. **Get a Gemini API key** at
   [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (free
   tier works fine for a class FAQ bot).
2. In the [Vercel dashboard](https://vercel.com/new), click **Add New →
   Project** and **import this GitHub repository**.
3. Vercel auto-detects **Next.js** — leave the framework preset and build
   settings exactly as detected. The Python function in `api/` is picked up
   automatically (dependencies come from `pyproject.toml` + `uv.lock`).
4. Under **Environment Variables**, add:

   | Name             | Value               |
   | ---------------- | ------------------- |
   | `GOOGLE_API_KEY` | your Gemini API key |

   (Optional: `GEMINI_MODEL` to use a different Gemini model.)

5. Click **Deploy**.

### Your URL

Vercel names the project after the repo, so the chat will live at:

**`https://ds295r-info-chat.vercel.app`**

If that subdomain is already taken, Vercel appends a suffix (e.g.
`ds295r-info-chat-abc123.vercel.app`) — the exact URL is shown on the
project's **Domains** page after the first deploy. Share that link (or add
a custom domain in **Settings → Domains**) with your students. No further
configuration is needed: the frontend automatically talks to the backend on
the same domain under `/api`.

## Local development

Prerequisites: [Node.js](https://nodejs.org) 20+, [pnpm](https://pnpm.io)
(`npm i -g pnpm`), and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
pnpm install   # frontend deps
uv sync        # backend deps (creates .venv)
```

Run the two dev servers in separate terminals:

```bash
pnpm dev:api   # FastAPI on http://localhost:8000
pnpm dev       # Next.js on http://localhost:3000 (proxies /api to :8000)
```

Then open <http://localhost:3000>. Set your key first
(`GOOGLE_API_KEY`, e.g. in a `.env` file — see `.env.example`), or run the
backend without any key using the canned fake model:

```bash
# PowerShell                          # bash/zsh
$env:FAKE_MODEL = "1"; pnpm dev:api   FAKE_MODEL=1 pnpm dev:api
```

### Tests

```bash
uv run pytest                 # backend protocol tests (no API key needed)
node scripts/e2e-smoke.mjs    # browser smoke test (needs both dev servers
                              # running with FAKE_MODEL=1, and
                              # `npx playwright install chromium` once)
```

## How it works

The frontend is a vendored copy of LangChain's agent-chat-ui with small
changes:

- The API URL defaults to `<origin>/api` and the assistant id to `agent`,
  so no setup form is shown and no `NEXT_PUBLIC_*` env vars are required.
- Every message submit sends the **full conversation history**, because the
  serverless backend is stateless between requests.
- File upload and tool-call controls were removed (this bot answers from
  text only), and the branding was changed.

The backend (`api/index.py`) implements the handful of LangGraph Platform
REST endpoints that `useStream` needs (`/info`, `/threads`,
`/threads/search`, `/threads/{id}/history`, `/threads/{id}/runs/stream`)
and streams the model's answer back as `values` SSE events.

### Limitations (by design, to stay simple)

- **Thread history is ephemeral.** Threads live in the function instance's
  memory, so the sidebar's past conversations disappear when the serverless
  instance recycles or the page reloads. The active conversation always
  works because the client resends the full history each turn.
- One assistant, no auth: anyone with the URL can chat. Don't put secrets
  in `course_info.md`.

## Configuration reference

| Env var                    | Default                 | Purpose                                          |
| -------------------------- | ----------------------- | ------------------------------------------------ |
| `GOOGLE_API_KEY`           | — (required in prod)    | Gemini API key                                   |
| `GEMINI_MODEL`             | `gemini-3.1-flash-lite` | Gemini model id                                  |
| `FAKE_MODEL`               | unset                   | `1` = canned responses, no key needed (dev only) |
| `NEXT_PUBLIC_API_URL`      | `<origin>/api`          | Point the UI at a different LangGraph server     |
| `NEXT_PUBLIC_ASSISTANT_ID` | `agent`                 | Assistant/graph id sent by the UI                |
