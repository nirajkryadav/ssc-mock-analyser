# Deploying

Frontend on Netlify, backend on Cloud Run, reached through a Netlify proxy so
the browser only ever talks to one origin.

```
  browser ──► https://your-site.netlify.app          (static HTML/CSS/JS)
                     │  /api/*  rewritten at the edge
                     ▼
              https://ssc-api-xxxx.a.run.app         (FastAPI + Tesseract)
```

The backend holds an uploaded PDF in memory for the length of one request and
discards it. What it keeps is the scored result of each mock (the question
records, not the PDF) in Cloud Firestore, so the page can show your progress and
reopen any past mock. The browser never talks to Firestore: only the Cloud Run
service does, through its service account, so the database stays closed to the
internet.

**There is no sign-in.** Anyone who has the site's URL can see the saved mocks
(including the name and roll number printed on the sheet) and delete them. That
is the trade made for a personal tool; keep the URL to yourself.

## Why not Streamlit on Netlify

Netlify serves static files and short-lived serverless functions, with no
Python runtime. Streamlit needs a long-running Python process and an open
WebSocket per visitor, so it cannot run there in any form. `app.py` stays in
the repo as the local tool; `web/` plus `api/` is the deployed version.

## Why Cloud Run and not Cloud Functions

Reading a screen-capture response sheet — one bitmap per page, no text layer —
needs Tesseract, a system binary. The Firebase Functions Python runtime cannot
install system packages. Cloud Run takes a `Dockerfile`, so it can. It still
scales to zero, so an idle service costs nothing.

Both sit under the same Firebase/Google Cloud project and the same Blaze
billing account. **Blaze (pay-as-you-go) is required** — the free Spark plan
cannot deploy functions or services at all. Blaze keeps the free tier, and for
one person analysing mocks the realistic bill is zero.

---

## 1. Backend → Cloud Run

Once, to pick the project and enable the services:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
                       artifactregistry.googleapis.com firestore.googleapis.com
```

Create the database the saved mocks live in (once per project):

```bash
gcloud firestore databases create --location=asia-south1
```

Firestore's security rules can stay at their default of denying every client.
The Cloud Run service reaches the database through IAM, which those rules do
not apply to.

Deploy (builds the container from the `Dockerfile` in this folder):

```bash
gcloud run deploy ssc-api \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --memory 1Gi --cpu 1 \
  --timeout 300 \
  --concurrency 1 \
  --max-instances 3 \
  --set-env-vars RECORDS_STORE=firestore
```

`--max-instances 3` is a cost ceiling, not a performance setting: it is the one
flag that stops a runaway loop becoming a bill. The first build takes 3–5
minutes; later ones are faster.

The command prints a service URL like `https://ssc-api-abc123-el.a.run.app`.
Check it:

```bash
curl https://ssc-api-abc123-el.a.run.app/api/health
# {"ok":true,"ocr_backends":["tesseract"],"max_upload_bytes":31457280}
curl https://ssc-api-abc123-el.a.run.app/api/records
# {"records":[]}
```

If `/api/records` returns a 500 mentioning permissions or a missing database,
the Firestore database was not created, or the service is running as an account
without the *Cloud Datastore User* role.

If `ocr_backends` is empty, Tesseract did not install — the container was built
from something other than this `Dockerfile`.

### Why one request per instance

`--concurrency 1` looks wasteful and is not. OCR is CPU-bound and single
threaded; two requests sharing one vCPU take twice as long each rather than
overlapping. Cloud Run starts another instance instead, and both finish in
normal time.

## 2. Frontend → Netlify

Put your Cloud Run URL into `netlify.toml`, replacing the placeholder:

```toml
[[redirects]]
  from = "/api/*"
  to = "https://ssc-api-abc123-el.a.run.app/api/:splat"
  status = 200
  force = true
```

Then:

```bash
netlify login
netlify deploy --dir web --prod
```

Or connect the repository at app.netlify.com and let it deploy on push —
`netlify.toml` already sets `publish = "web"` and needs no build step.

That proxy is doing real work: the browser calls `/api/...` on the Netlify
domain, so there is no cross-origin request, no preflight, and no backend URL
compiled into the JavaScript. Change the backend and you edit one line.

## 3. Lock down CORS

The proxy means the browser never sends an `Origin` the backend has to accept,
but a direct call would. Pin it to your site:

```bash
gcloud run services update ssc-api --region asia-south1 \
  --set-env-vars ALLOWED_ORIGINS=https://your-site.netlify.app
```

## Running both locally

```bash
# backend
.venv/bin/uvicorn api.main:app --reload --port 8080

# frontend, in another terminal
cd web && python3 -m http.server 8888
```

Open <http://127.0.0.1:8888>. `web/config.js` points at port 8080 automatically
when the page is served from localhost, and the backend allows that origin by
default.

Locally, saved mocks go to `.records/records.json` instead of Firestore (set
`RECORDS_FILE` to put them elsewhere). That file is git-ignored.

## Costs

| | |
|---|---|
| Netlify | Free tier: 100 GB bandwidth. The site is ~30 KB. |
| Cloud Run | Free tier: 180,000 vCPU-seconds/month. One mock ≈ 15 vCPU-seconds. |
| Cloud Build | Free tier: 120 build-minutes/day. A deploy takes ~4. |
| Artifact Registry | 0.5 GB free; the image is ~400 MB. |
| Firestore | Free tier: 1 GB stored, 50,000 reads/day. One mock is ~60 KB. |

Analysing a mock a day stays inside every free tier. Blaze still needs a card
on file, and setting a budget alert in the Cloud console is worth the minute it
takes.

## If something breaks

| Symptom | Cause |
|---|---|
| Frontend says it cannot reach the analyser | Backend not deployed, or the `netlify.toml` URL still says `REPLACE-ME`. |
| First request after a quiet spell is slow | Cold start. Scale-to-zero costs about 5 seconds on the first hit. |
| `413` on upload | A single PDF over 30 MB. Upload one PART at a time; the frontend already does. |
| `422` saying the PDF has only page images | OCR missing in the container — check `/api/health`. |
| Analysis works but the text is garbled | Expected on the OCR path. Scores come from colours and are unaffected. |
| Deploy refused on the Spark plan | Upgrade the project to Blaze. |
