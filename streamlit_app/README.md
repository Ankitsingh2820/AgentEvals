# AgentEval: online demo (Streamlit)

**Live: https://agentevals-o7cmqmhhycu28cappkrxggb.streamlit.app/**

A lightweight, public version of AgentEval. It runs the **real engine** (agent loop,
evaluators, LLM judge, experiment statistics, costs) in one Streamlit app, without the API
server, PostgreSQL, Redis or the worker. For the full platform, see the
[main README](../README.md).

- Every visitor gets a **private, temporary** in-memory workspace with demo data.
- Everything runs on the free **mock model** by default.
- Visitors can connect **their own** Groq, OpenAI or Anthropic key. It is kept only in
  their session and never stored, logged or shared. The host's keys are never used, even
  if present in the environment or a `.env` file.

## Run locally

```bash
cd streamlit_app
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py        # http://localhost:8501
```

## Deploy on Streamlit Community Cloud

1. Push the repository to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), create an app: pick the repo, branch
   `main`, main file path `streamlit_app/app.py`, Python 3.12.
3. Do **not** add any API keys as secrets: visitors bring their own.

## Tests

```bash
pip install pytest && python -m pytest tests
```
