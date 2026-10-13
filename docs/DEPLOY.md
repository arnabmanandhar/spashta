# Hosting Spashta publicly for free, without leaking keys

## Where secrets live

Spashta reads every secret from environment variables at run time:

| Variable | Used by | Needed? |
|---|---|---|
| `GEMINI_API_KEY`, `GROQ_API_KEY`, ... | `anchor.llm.connection_from_env()` for the optional claim review | Only for the LLM review |
| `SPASHTA_ADMIN_TOKEN` | `POST /api/sources`, `POST /api/ingest`, and LLM review requests | Yes, for a hosted instance |

Nothing in the repository holds a key. `.env` and `.env.*` are git-ignored
(except `.env.example`, which has empty values), the Dockerfile bakes no
secrets into the image, and API responses and errors never include a key
(a failed model call reports only its kind, such as `auth` or `rate_limit`).
The `gsk_test_key_123456` strings in the tests are fake.

Rules that keep it that way:

1. Put keys only in the host's **secret** settings (Hugging Face Space
   secrets, GitHub Actions secrets), never in a file you commit, a
   Dockerfile, or a public "variable".
2. Use a free-tier model key with **no billing attached** (Gemini from
   Google AI Studio, or Groq). If it ever leaks, the worst case is a used-up
   quota, not a bill. Rotate it at the provider if you suspect a leak.
3. Before every push, `git status` and `git diff --cached` should show no
   `.env` or `*.db` file. To scan the whole history:
   `git log --all -p | findstr "AIza gsk_ hf_ sk-proj"` (PowerShell/cmd) or
   `git log --all -p | grep -E "AIza|gsk_|hf_|sk-proj"` (bash). Only the fake
   test key should show up.
4. If a real key was ever committed, revoke it at the provider first.
   Deleting it from git afterwards does not help, because the history
   is already public.

## Protecting the public API from quota abuse

The defaults are safe for a public URL:

- **LLM review is off for the public.** `"llm": true` returns 403 unless the
  request carries the admin token, or you set `SPASHTA_PUBLIC_LLM=1`.
- **Daily LLM budget.** `SPASHTA_LLM_DAILY_LIMIT` (default 50) caps model
  calls per UTC day across everyone. Reviews of an unchanged article come
  from the cache and are free.
- **Rate limits.** `SPASHTA_RATE_PER_MIN` (default 20) per client, and
  `SPASHTA_GLOBAL_RATE_PER_MIN` (default 120) for everyone together.
  Behind a host's proxy, set `SPASHTA_TRUST_PROXY=1` (the Dockerfile does)
  so the per-client limit uses the real visitor address. Without it, all
  visitors share the proxy's address and one limit.
- **Admin endpoints** stay disabled until `SPASHTA_ADMIN_TOKEN` is set.
- **Fetching** refuses anything that isn't a public address on a web port
  (private, loopback, link-local, cloud metadata such as 169.254.169.254,
  IPv4-mapped and 6to4 forms) on every redirect hop, connects to the
  exact address it checked so DNS rebinding can't redirect it, gives one
  fetch 20 seconds in total, caps page size at 5 MB and the analysed text
  at 60,000 characters, and honours robots.txt.
- **Request limits.** Bodies over 256 KB get 413 (and POSTs must send a
  length); an analysis gets `SPASHTA_REQUEST_TIMEOUT` seconds (30) before
  504; at most `SPASHTA_MAX_CONCURRENT` (4) run at once, more get 503.
- **No reader addresses in logs.** Each request is logged with a hash of
  the client address under a key that changes every UTC day and is never
  stored, so addresses can't be recovered. The Dockerfile turns off
  uvicorn's own access log, which would record them.

## Recommended free host: Hugging Face Spaces (Docker)

Why: free CPU with 16 GB RAM (enough for the e5 embedder later), a
built-in secrets UI, HTTPS, and a URL anyone can open. Caveats: a free
Space sleeps after about 48 hours without visitors, and its disk is
temporary, so the index is rebuilt by the scheduled ingest after a
restart.

1. Create a Space at huggingface.co/new-space: SDK **Docker**, blank
   template, public.
2. In the Space's **Settings → Variables and secrets**, add **secrets**:
   - `SPASHTA_ADMIN_TOKEN`: generate one with
     `python -c "import secrets; print(secrets.token_urlsafe(32))"`
   - `GEMINI_API_KEY` (optional)
3. Deploy from GitHub automatically:
   - On huggingface.co/settings/tokens, create a **write** token.
   - In the GitHub repo's **Settings → Secrets and variables → Actions**:
     add the secret `HF_TOKEN`, and the variable `HF_SPACE` set to
     `your-hf-username/your-space-name`.
   - Every push to `main` now runs `.github/workflows/deploy-space.yml`,
     which pushes the code to the Space with the front matter the Space
     needs. It can also be run by hand from the Actions tab.
4. Keep the index fresh: in the same GitHub settings, add the secret
   `SPASHTA_ADMIN_TOKEN` (same value as on the Space) and the variable
   `SPASHTA_URL` (`https://your-hf-username-your-space-name.hf.space`).
   `.github/workflows/ingest.yml` then polls the seeded outlets every
   three hours.

Both workflows do nothing until their variables are set, so the public
repo stays green without them.

### Other free options

- **Render** (free web service): connect the GitHub repo, choose
  Docker, set the same variables as secret environment variables, and set
  `PORT` to what Render expects (it injects `PORT`; the Dockerfile reads
  it). The instance has 512 MB of RAM and sleeps after 15 idle minutes.
- **Your own computer plus a tunnel** (Cloudflare Tunnel): free and
  persistent, but only up while your computer is on.

## Running the image locally

```bash
docker build -t spashta .
docker run --rm -p 7860:7860 --env-file .env spashta
```

Open http://localhost:7860.
