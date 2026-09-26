# AgentEval dashboard

React + TypeScript + Tailwind + Recharts front end for the AgentEval API.

```bash
npm install
npm run dev      # http://localhost:5173, proxies /api to http://localhost:8000
                 # (override with AGENTEVAL_API_URL=http://host:port)
npm run lint
npm run build
```

In Docker (`docker compose up` from the repo root) it is served by nginx on port 3000
(`AGENTEVAL_WEB_PORT` to change), which proxies `/api/` to the API container.

Chart colors, marks and interaction follow the dataviz method in `src/index.css` and
`src/components/charts.tsx`: validated categorical colors (blue, orange) in both themes,
one y-axis per chart, a crosshair tooltip, a table view for every chart, and status shown
with an icon and a label, never color alone.
