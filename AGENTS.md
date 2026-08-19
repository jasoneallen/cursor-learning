# AI Job Operator

A personal, local-first "AI Job Operator". It analyzes jobs against a candidate
profile, tracks a local pipeline, and discovers jobs from a local JSON file or the
public Greenhouse and Lever job boards. GPT (OpenAI) runs only on demand.

- `operator_app.py` — Streamlit UI (the main app). Tabs: Job Matcher, Pipeline, Job Discovery.
- `hello.py` — small interactive CLI unrelated to the operator app.
- `job_matcher.py` — skill matching + optional OpenAI analysis (`analyze_with_ai`).
- `job_prefilter.py` — local scoring/ingest logic (no OpenAI).
- `greenhouse_source.py`, `lever_source.py`, `job_sources.py`, `all_live_source.py` — live job board adapters.
- `job_source_config.py` — configured Greenhouse boards / Lever sites.
- `test_ai_eligibility.py` — regression tests (never call OpenAI).

## Cursor Cloud specific instructions

- Python 3.12 with a virtualenv at `.venv`. The update script creates/refreshes it and
  installs `requirements.txt`. Activate it before running anything: `source .venv/bin/activate`.
- Run tests: `python test_ai_eligibility.py` (plain script, no pytest; prints `all tests passed`).
  These tests assert OpenAI is never called, so they need no API key.
- Quick sanity/lint check: `python -m py_compile *.py`. There is no configured linter/formatter.
- Run the app (dev): `streamlit run operator_app.py`. For headless VM use:
  `streamlit run operator_app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true`.
  Health check: `curl http://localhost:8501/_stcore/health` returns `ok`.
- `OPENAI_API_KEY` is OPTIONAL and only needed for the on-demand "Analyze Job" / "Analyze
  selected job" buttons. The whole app, tests, and Job Discovery run without it. If you need
  AI analysis, put `OPENAI_API_KEY=sk-...` in a local `.env` (gitignored) — do not commit it.
- Best no-key end-to-end demo: Job Discovery tab → source `Greenhouse` → `Fetch Jobs`. This
  hits the live public Greenhouse/Lever APIs (`boards-api.greenhouse.io`, `api.lever.co`) and
  runs the local prefilter/scoring — it requires outbound network but no OpenAI key.
- Runtime writes live under `data/` (e.g. `data/jobs.json`) and `private/` — both gitignored.
  The candidate profile is read from `private/profile.txt` when present.
