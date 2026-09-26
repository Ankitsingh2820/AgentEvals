"""AgentEval: lightweight online demo.

Runs the real AgentEval engine in-process with a private, temporary workspace per
visitor. The full platform (API, dashboard, worker, PostgreSQL, Redis) runs locally with
Docker: see the repository README.

    streamlit run streamlit_app/app.py
"""

import engine as ae
import pandas as pd
import streamlit as st

REPO_URL = "https://github.com/Ankitsingh2820/AgentEvals"

st.set_page_config(page_title="AgentEval demo", layout="wide")


def workspace() -> ae.Workspace:
    if "ws" not in st.session_state:
        with st.spinner("Preparing your private demo workspace..."):
            st.session_state.ws = ae.new_workspace()
    return st.session_state.ws


ws = workspace()


def money(v) -> str:
    if v is None:
        return "unpriced"
    v = float(v)
    return "$0" if v == 0 else (f"${v:.4f}" if v >= 0.0001 else f"${v:.2e}")


def ms(v) -> str:
    return "n/a" if v is None else (f"{v / 1000:.2f} s" if v >= 1000 else f"{v:.0f} ms")


def pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def verdict_box(verdict: str | None, reasons: list[str]) -> None:
    text = "; ".join(reasons)
    if verdict == "PASS":
        st.success(f"**PASS**: {text}", icon="✅")
    elif verdict == "FAIL":
        st.error(f"**FAIL**: {text}", icon="❌")
    else:
        st.warning(f"**{verdict or 'NO VERDICT'}**: {text}", icon="❔")


def version_label(v) -> str:
    return f"{v.agent.name} · v{v.version} · {v.provider}/{v.model}"


def provider_options() -> list[str]:
    return ["mock", *ae.connected(ws)]


def provider_label(p: str) -> str:
    return "Mock (free, offline)" if p == "mock" else ae.REAL_PROVIDERS[p]


def pick_model(provider: str, key: str) -> str | None:
    choices = ae.model_choices(ws, provider)
    default = next(
        (
            i
            for i, m in enumerate(choices)
            if m in ("openai/gpt-oss-20b", "gpt-4.1-mini", "claude-haiku-4-5")
        ),
        0,
    )
    return st.selectbox("Model", choices, index=default, key=key) if choices else None


# --- Sidebar ------------------------------------------------------------------------------

with st.sidebar:
    st.title("AgentEval")
    st.caption("Evaluate and optimize LLM agents on measured quality, cost and latency.")
    st.link_button("Full project on GitHub", REPO_URL, width="stretch")
    st.info(
        "This workspace is **private and temporary**: only you see it, and it resets "
        "when you close the tab. Everything runs on a free **mock model** until you "
        "connect your own key.",
        icon="🔒",
    )

    st.subheader("Use a real model (optional)")
    provider = st.selectbox(
        "Provider", list(ae.REAL_PROVIDERS), format_func=lambda p: ae.REAL_PROVIDERS[p]
    )
    key = st.text_input(
        "Your API key",
        type="password",
        key=f"key_{provider}",
        help="Used only in this browser session. Never stored or logged.",
    )
    if st.button("Connect", width="stretch"):
        try:
            with st.spinner("Checking the key (listing models, free)..."):
                n = len(ae.connect(ws, provider, key))
            st.success(f"Connected: {n} models available.")
        except ae.DemoError as e:
            st.error(str(e))
    for p in ae.connected(ws):
        cols = st.columns([3, 2])
        cols[0].write(f"✓ {ae.REAL_PROVIDERS[p]}")
        if cols[1].button("Disconnect", key=f"disc_{p}"):
            ae.disconnect(ws, p)
            st.rerun()
    st.caption(
        "Real calls are billed to **your** account. Runs here are small: one input, or "
        f"5 test cases x up to {ae.MAX_REPETITIONS} repetitions."
    )
    if st.button("Reset workspace", width="stretch"):
        st.session_state.pop("ws", None)
        st.rerun()

overview, run_tab, eval_tab, exp_tab, about = st.tabs(
    ["Overview", "Run an agent", "Evaluate", "Compare versions", "About"]
)

# --- Overview -----------------------------------------------------------------------------

with overview:
    st.header("Can this agent do the same job for less money and time?")
    st.write(
        "AgentEval records every model and tool call an agent makes, scores its answers, "
        "prices every run, and compares versions on the same test cases with a "
        "**PASS / FAIL / INCONCLUSIVE** verdict. Your workspace starts with demo data: two "
        "agents, a 5-company test set, an evaluation and an experiment."
    )
    all_runs = ae.runs(ws)
    done = [r for r in all_runs if r.status != "running"]
    ok = [r for r in done if r.status == "succeeded"]
    costed = [float(r.estimated_total_cost) for r in done if r.cost_status == "complete"]
    c = st.columns(5)
    c[0].metric("Agents", len(ae.agents(ws)), border=True)
    c[1].metric("Runs", len(done), border=True)
    c[2].metric("Success rate", pct(len(ok) / len(done) if done else None), border=True)
    c[3].metric("Avg cost / run", money(sum(costed) / len(costed) if costed else None), border=True)
    lat = sorted(r.total_latency_ms for r in ok)
    c[4].metric("Median latency", ms(lat[len(lat) // 2] if lat else None), border=True)

    exps = ae.experiments(ws)
    if exps:
        st.subheader("Latest experiment")
        e = exps[0]
        verdict_box(e.verdict, (e.comparison or {}).get("reasons", []))
        st.caption(f"{e.name}: {e.description or ''} See **Compare versions** to run your own.")

    st.subheader("Recent runs")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "agent": r.agent_version.agent.name,
                    "model": r.agent_version.model,
                    "input": r.input,
                    "status": r.status,
                    "latency": ms(r.total_latency_ms),
                    "cost": money(r.estimated_total_cost),
                    "tool calls": r.tool_call_count,
                }
                for r in all_runs[:15]
            ]
        ),
        hide_index=True,
        width="stretch",
    )

# --- Run an agent -------------------------------------------------------------------------

with run_tab:
    st.header("Run an agent")
    st.write("Pick an agent, give it a company name, and see every step it takes.")

    with st.expander("Create a new agent"):
        with st.form("new_agent"):
            name = st.text_input("Name", "My research agent")
            prov = st.selectbox(
                "Provider", provider_options(), format_func=provider_label, key="na_provider"
            )
            model = pick_model(prov, "na_model")
            prompt = st.text_area(
                "System prompt",
                "Use the company_lookup tool to research the company, then say what it "
                "does, its industry and approximate size.",
            )
            tools = st.multiselect("Tools", ae.TOOLS, default=["company_lookup"])
            if st.form_submit_button("Create agent"):
                try:
                    v = ae.create_agent(ws, name, prov, model, prompt, tools)
                    st.session_state.run_version = v.id
                    st.success(f"Created {v.agent.name}.")
                except ae.DemoError as e:
                    st.error(str(e))
        if not ae.connected(ws):
            st.caption("Connect a key in the sidebar to create agents on real models.")

    all_versions = [v for a in ae.agents(ws) for v in a.versions]
    ids = [v.id for v in all_versions]
    default = (
        ids.index(st.session_state.get("run_version"))
        if st.session_state.get("run_version") in ids
        else 0
    )
    vid = st.selectbox(
        "Agent version",
        ids,
        index=default,
        format_func=lambda i: version_label(next(v for v in all_versions if v.id == i)),
    )
    text = st.text_input(
        "Input",
        "Acme Corp",
        help="The demo tool knows: Acme Corp, Globex Analytics, "
        "Initech Health, Umbrella Logistics. Try an unknown name too.",
    )
    if st.button("Run agent", type="primary"):
        try:
            with st.spinner("Running..."):
                st.session_state.last_run = ae.run_agent(ws, vid, text).id
        except ae.DemoError as e:
            st.error(str(e))

    if rid := st.session_state.get("last_run"):
        run = ws.db.get(ae.models.Run, rid)
        st.subheader("Result")
        if run.status == "succeeded":
            st.success(
                f"Succeeded on {run.agent_version.provider}/{run.agent_version.model}", icon="✅"
            )
        else:
            st.error(f"Failed: {run.error}", icon="❌")
        m = st.columns(5)
        m[0].metric("Latency", ms(run.total_latency_ms), border=True)
        m[1].metric("Cost", money(run.estimated_total_cost), border=True)
        m[2].metric("Tokens in / out", f"{run.input_tokens} / {run.output_tokens}", border=True)
        m[3].metric("LLM calls", run.llm_call_count, border=True)
        m[4].metric("Tool calls", run.tool_call_count, border=True)
        st.markdown("**Answer**")
        with st.container(border=True):
            st.markdown(run.final_output or "_(no answer)_")
        st.markdown("**Every step** (time from run start, how long it took, tokens, cost)")
        st.dataframe(pd.DataFrame(ae.step_rows(run)), hide_index=True, width="stretch")
        for s in run.steps:
            if s.tool_call:
                with st.expander(f"Tool call #{s.sequence}: {s.tool_call.tool_name}"):
                    st.json(
                        {
                            "arguments": s.tool_call.arguments,
                            "result": s.tool_call.result,
                            "error": s.error,
                        }
                    )

# --- Evaluate -----------------------------------------------------------------------------

with eval_tab:
    st.header("Evaluate an agent on a test set")
    ds = ae.dataset(ws)
    st.write(
        f"The agent answers all **{len(ds.cases)} test cases** and every answer is scored "
        "by the evaluators you pick."
    )
    with st.expander("See the test cases"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "case": c.case_key,
                        "input": c.input,
                        "must mention": ", ".join(c.labels.get("must_mention", [])),
                    }
                    for c in ds.cases
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    with st.expander("Add a real LLM judge"):
        if not ae.connected(ws):
            st.caption(
                "Connect a key in the sidebar first. The built-in judge is a mock "
                "that always scores 4/5."
            )
        else:
            jp = st.selectbox(
                "Judge provider", ae.connected(ws), format_func=provider_label, key="judge_provider"
            )
            jm = pick_model(jp, "judge_model")
            crit = st.selectbox(
                "Criterion",
                [
                    "relevance",
                    "correctness",
                    "completeness",
                    "groundedness",
                    "instruction_following",
                ],
            )
            if st.button("Add judge"):
                try:
                    ae.add_judge(ws, jp, jm, crit)
                    st.success("Judge added: select it below.")
                except ae.DemoError as e:
                    st.error(str(e))

    evs = ae.evaluators(ws)
    ev_label = {e.id: f"{e.name} ({e.type})" for e in evs}
    all_versions = [v for a in ae.agents(ws) for v in a.versions]
    e_vid = st.selectbox(
        "Agent version",
        [v.id for v in all_versions],
        key="eval_version",
        format_func=lambda i: version_label(next(v for v in all_versions if v.id == i)),
    )
    chosen = st.multiselect(
        "Evaluators",
        [e.id for e in evs],
        default=[e.id for e in evs if e.type != "llm_judge"],
        format_func=lambda i: ev_label[i],
    )
    reps = st.slider(
        "Repetitions per case",
        1,
        ae.MAX_REPETITIONS,
        1,
        help="Models vary between runs; repeating shows how much.",
    )
    if st.button("Run evaluation", type="primary"):
        try:
            with st.spinner("Running the agent on every case and scoring it..."):
                st.session_state.last_eval = ae.run_evaluation(ws, e_vid, chosen, reps).id
        except ae.DemoError as e:
            st.error(str(e))

    if eid := st.session_state.get("last_eval"):
        ev = ws.db.get(ae.models.Evaluation, eid)
        s = ev.summary or {}
        if ev.status != "completed":
            st.error(f"Evaluation {ev.status}: {ev.error}")
        elif s:
            q = s["quality"]
            m = st.columns(4)
            m[0].metric("Quality (mean score)", pct(q["score"]["mean"]), border=True)
            m[1].metric("Pass rate", pct(q["pass_rate"]), border=True)
            m[2].metric("Agent cost", money(s["estimated_total_cost"]), border=True)
            m[3].metric("Judge cost", money(s["judge_estimated_cost"]), border=True)
            st.markdown("**Per evaluator**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "evaluator": v["name"],
                            "mean score": pct(v["score"]["mean"]),
                            "pass rate": pct(v["pass_rate"]),
                            "scored": v["scored"],
                            "skipped": v["skipped"],
                            "errors": v["errors"],
                        }
                        for v in s["evaluators"].values()
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            st.markdown("**Every verdict, with the reason**")
            st.dataframe(pd.DataFrame(ae.results(ws, eid)), hide_index=True, width="stretch")

# --- Compare versions ---------------------------------------------------------------------

with exp_tab:
    st.header("Compare two versions")
    st.write(
        "Both versions answer the same test cases (interleaved, repeated). AgentEval "
        "compares quality, cost, latency and errors, and a candidate only **PASSes** if "
        "every criterion holds with enough evidence."
    )
    agent_list = ae.agents(ws)
    aid = st.selectbox(
        "Agent",
        [a.id for a in agent_list],
        format_func=lambda i: next(a.name for a in agent_list if a.id == i),
    )
    vs = ae.versions(ws, aid)
    fmt = {v.id: f"v{v.version} · {v.provider}/{v.model}" for v in vs}
    col1, col2 = st.columns(2)
    base = col1.selectbox("Baseline", [v.id for v in vs], format_func=lambda i: fmt[i])
    with col2:
        mode = st.radio(
            "Candidate", ["Existing version", "New version with another model"], horizontal=True
        )
        if mode == "Existing version":
            cand = st.selectbox(
                "Candidate version",
                [v.id for v in vs],
                index=min(1, len(vs) - 1),
                format_func=lambda i: fmt[i],
            )
            new_model = None
        else:
            cand = None
            cp = st.selectbox(
                "Provider", provider_options(), format_func=provider_label, key="cand_provider"
            )
            new_model = (cp, pick_model(cp, "cand_model"))

    evs = ae.evaluators(ws)
    x_evals = st.multiselect(
        "Evaluators",
        [e.id for e in evs],
        key="exp_evals",
        default=[e.id for e in evs if e.type != "llm_judge"],
        format_func=lambda i: f"{next(e.name for e in evs if e.id == i)}",
    )
    c = st.columns(4)
    drop = c[0].number_input("Max quality drop (points)", 0.0, 100.0, 2.0, 0.5)
    cheaper = c[1].number_input(
        "Min cost reduction (%)", 0.0, 100.0, 0.0, 5.0, help="0 = don't require savings"
    )
    err = c[2].number_input("Max error rate", 0.0, 1.0, 0.05, 0.05)
    x_reps = c[3].slider(
        "Repetitions",
        1,
        ae.MAX_REPETITIONS,
        2,
        key="exp_reps",
        help="5 cases x 2 repetitions = 10 comparisons, the minimum for a PASS.",
    )
    if st.button("Run experiment", type="primary"):
        try:
            if new_model:
                cand = ae.new_version(ws, aid, *new_model).id
            criteria = {"max_quality_drop_points": drop, "max_error_rate": err}
            if cheaper > 0:
                criteria["min_cost_reduction_pct"] = cheaper
            with st.spinner("Running both versions on every case..."):
                st.session_state.last_exp = ae.run_experiment(
                    ws, base, cand, x_evals, x_reps, criteria
                ).id
        except ae.DemoError as e:
            st.error(str(e))

    if xid := st.session_state.get("last_exp"):
        e = ws.db.get(ae.models.Experiment, xid)
        if e.status != "completed":
            st.error(f"Experiment {e.status}: {e.error}")
        else:
            comp = e.comparison
            verdict_box(e.verdict, comp["reasons"])
            for ch in comp["checks"]:
                icon = {"PASS": "✅", "FAIL": "❌"}.get(ch["status"], "❔")
                st.write(f"{icon} **{ch['name']}** ({ch['rule']}): {ch['reason']}")
            m = comp["metrics"]
            L = m["latency_ms"]
            rel = m["reliability"]
            rows = [
                (
                    "Quality",
                    pct(m["quality"]["baseline"]),
                    pct(m["quality"]["candidate"]),
                    f"{m['quality']['change_points'] or 0:+.1f} pts",
                ),
                (
                    "Cost per run",
                    money(m["cost_per_run"]["baseline"]),
                    money(m["cost_per_run"]["candidate"]),
                    "n/a"
                    if m["cost_per_run"]["change_pct"] is None
                    else f"{m['cost_per_run']['change_pct']:+.1f}%",
                ),
                (
                    "Latency (median)",
                    ms(L["baseline"]["median"]),
                    ms(L["candidate"]["median"]),
                    "n/a"
                    if L["change_pct"]["median"] is None
                    else f"{L['change_pct']['median']:+.1f}%",
                ),
                (
                    "Error rate",
                    pct(rel["baseline_error_rate"]),
                    pct(rel["candidate_error_rate"]),
                    "",
                ),
                (
                    "Tokens per run",
                    f"{m['tokens_per_run']['baseline'] or 0:.0f}",
                    f"{m['tokens_per_run']['candidate'] or 0:.0f}",
                    "",
                ),
            ]
            st.dataframe(
                pd.DataFrame(rows, columns=["metric", "baseline", "candidate", "change"]),
                hide_index=True,
                width="stretch",
            )
            st.caption(
                f"{comp['pairs']} paired runs. The full dashboard also shows 95% "
                "confidence intervals, p95/p99 latency and per-case results."
            )

# --- About --------------------------------------------------------------------------------

with about:
    st.header("About this demo")
    st.markdown(f"""
This page runs the **real AgentEval engine**: the same agent loop, evaluators, LLM judge,
experiment statistics and cost calculation as the full platform, inside one lightweight
app with a private in-memory database per visitor.

**The full platform** (run it locally with Docker, see the [README]({REPO_URL})) adds:

- a React dashboard with trend charts, run timelines and experiment details
- a REST API with API-key authentication and rate limits
- a background worker (Celery + Redis) that resumes after crashes
- PostgreSQL storage, OpenTelemetry tracing and structured logs
- optimization recommendations (parallel tools, caching, routing) you can apply in a click
- a CI pipeline with unit, integration, evaluation and browser tests

**Honest numbers:** the mock model's scores and costs are placeholders that demonstrate the
pipeline. Connect your own key to measure a real model.
""")
