// Where the backend lives.
//
// In production this stays empty: netlify.toml proxies /api/* to Cloud Run at
// the edge, so the browser only ever calls the Netlify origin and there is no
// CORS preflight and no URL baked into the build.
//
// Served from localhost it points at a backend running locally, so the
// frontend can be developed without deploying anything.
window.SSC_API_BASE = window.SSC_API_BASE ??
  (["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:8080"
    : "");
