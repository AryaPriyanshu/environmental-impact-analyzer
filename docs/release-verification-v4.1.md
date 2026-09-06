# Luma v4.1 release verification

## Release candidate

- Application version: **4.1.0**; scoring version: **4.1**.
- Source snapshot: `511869fd34bfc302`, generated **2026-08-23**.
- Effective catalogue: **13,143 records**, including 13 reviewed identity supplements.
- Runtime image: `python:3.11.16-slim-bookworm`; Streamlit **1.54.0**; runtime packages pinned in `requirements.txt`.
- Current release work started from Git revision `830f26d02d5f564c4a3d3101eff05b598ecd4a41`.

## Verification evidence

| Check | Result | Scope |
|---|---|---|
| Full suite in production container, 2026-09-06 | **104 passed**, no pytest warnings; 17.21 seconds | Python 3.11.16, Streamlit 1.54.0, all pinned runtime requirements plus test dependencies |
| Container dependency check | **Passed** | No broken requirements; existing runtime pins remained satisfied |
| Local regression suite, 2026-09-03 | **104 passed** | Older local Python 3.9 environment; one LibreSSL compatibility warning; the container run above is the release reference |
| Compilation, patch whitespace and Compose configuration | **Passed** | `compileall`, `git diff --check`, `docker compose config -q` |
| Offline data monitor, 2026-09-02 UTC | **Healthy** | Counts, freshness, provenance URLs, required schema, entity IDs, model snapshot and distribution checks |
| Production UI and API runtime | **Healthy** | UI health, API readiness, snapshot agreement and non-root process |
| Browser review, 2026-09-02–03 UTC | **Passed for exercised flows** | Search, assessment, changing assumptions, comparisons, saving, report downloads, light/dark themes and 390×844 mobile layout |
| Final chart render after deprecation fix | **Passed** | MacBook Air M2 dashboard, visible chart, no fresh browser warnings/errors and no server deprecation warning |
| API browser-origin policy | **Passed** | Allowed origin received its CORS header; a different origin did not |

Browser testing covers the exercised interactions, not every possible device, input combination, browser or assistive technology. Automated tests cover scenario/project parsing, evidence boundaries, calculations, model fallback/governance, search, API behavior, reports, synchronization and monitoring. File imports and shared-state restoration also have automated coverage.

## Reproduce the container regression check

From the repository root, with Docker running:

```bash
docker build --tag luma-release:verify .
docker run --rm --security-opt no-new-privileges:true luma-release:verify \
  sh -c 'python -m pip install --user --no-cache-dir --no-warn-script-location -r requirements-dev.txt && python -m pip check && python -m pytest -q'
```

The test installation needs package-index access. It changes only the disposable container, not the image. CI repeats this check in the `container-test` job. Test-run elapsed time varies by machine.

For the two local services:

```bash
docker compose up --build -d
curl --fail http://localhost:8501/_stcore/health
curl --fail http://localhost:8000/health/ready
```

## Publication state at verification

On **2026-09-06**, GitHub `main` still pointed to `830f26d02d5f564c4a3d3101eff05b598ecd4a41`. The deployed API `/health` reported **4.0.0** with the same source snapshot. The v4.1 changes therefore require publication before they appear on the live site.

`render.yaml` enables automatic deployment. Merging the release into the tracked production branch may update the public UI and API; verify GitHub CI before merging. After deployment, `/health/ready` should report application version **4.1.0**, a ready database and model, and matching snapshot IDs. Retain the preceding deployed revision for rollback through the hosting platform.

## Data and model interpretation

The snapshot date remains August 23; a healthy freshness check does not mean continuous real-time coverage. Most devices do not have complete comparable lifecycle measurements. Unlisted names receive category or generic estimates. The neural model remains in experimental shadow mode, with no point-score contribution until validated against an external real-LCA benchmark. These limits remain visible in the application and README.
