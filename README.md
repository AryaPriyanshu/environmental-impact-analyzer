# Luma — Environmental Impact Analyser of Gadgets Using Deep Learning and AI

[![Verify application](https://github.com/AryaPriyanshu/environmental-impact-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/AryaPriyanshu/environmental-impact-analyzer/actions/workflows/ci.yml)

Luma is an end-to-end environmental intelligence application for smartphones, laptops, tablets, TVs, monitors, desktops, printers, network equipment, watches, headphones, speakers, streaming devices and related electronics.

It combines current public product records, a transparent lifecycle ledger, category-aware neural models, country-level electricity intensity and explicit uncertainty. It does **not** pretend that every public product record is a complete lifecycle assessment: observed fields, category estimates and user scenarios are marked separately.

## What is included

- **13,130 traceable source records** representing 13,126 resolved gadget entities from 604 manufacturers
- Full-text model/manufacturer search, source/category filters and CSV export
- Smartphone and tablet coverage from the EU EPREL regulatory registry
- Laptop, desktop, display, TV, printer and network-equipment energy records from ENERGY STAR
- French regulatory repairability data and iFixit teardown scores
- Apple and Samsung product environmental report values with links to the exact reports
- 298 brand/category repair profiles aggregated from 38,677 relevant Open Repair events
- Electricity carbon intensity for 238 countries and regions from Our World in Data / Ember
- Lifecycle dashboard covering manufacturing, use energy, lifespan, repairability, circularity/e-waste, battery and transport
- Category-aware neural model with per-category uncertainty calibration
- Greener-peer suggestions, saved shortlists, side-by-side comparison and PDF/CSV reports
- Shareable product/region/theme query state
- Responsive animated interface with persistent, accessible light and dark modes
- SQLite full-text catalogue, FastAPI service, health/metrics endpoints, Docker setup and automated refresh workflow

## Interface

Light and dark screenshots are generated during visual verification and stored in `docs/screenshots/`.

| Light | Dark |
|---|---|
| ![Luma light dashboard](docs/screenshots/dashboard-light.jpg) | ![Luma dark dashboard](docs/screenshots/dashboard-dark.jpg) |

The interface respects `prefers-reduced-motion`. Dark mode persists in session state and in `?theme=dark`, so shared views preserve the chosen appearance.

## Data snapshot

The bundled snapshot was generated on **2026-08-23**. Run the refresh command to rebuild it from the sources available at that time.

| Source | Raw/relevant records | What is observed |
|---|---:|---|
| [ENERGY STAR Computers V9.0](https://data.energystar.gov/d/rxdj-2c88) | 1,788 | Identity, type, certification dates, TEC energy, battery where published |
| [ENERGY STAR Displays V8.0](https://data.energystar.gov/d/qbg3-d468) | 2,846 | Identity, display properties and measured on-mode power |
| [ENERGY STAR Televisions V9.x](https://data.energystar.gov/d/pd96-rr3d) | 180 | Identity, screen details, on-mode power and annual energy |
| [ENERGY STAR Imaging Equipment](https://data.energystar.gov/d/t2v6-g4nf) | 2,745 | Identity, equipment type, sleep/standby power and dates |
| [ENERGY STAR Large Network Equipment](https://data.energystar.gov/d/n8cx-m62r) | 96 | Enterprise router/switch identity and measured load power |
| [EU EPREL smartphones and tablets](https://eprel.ec.europa.eu/screen/product/smartphonestablets20231669) | 2,358 | Identity, energy class, repairability, durability, battery endurance/cycles and software support |
| [French Repairability Index](https://www.data.gouv.fr/datasets/fichiers-consolides-des-donnees-respectant-le-schema-indice-de-reparabilite) | 3,416 relevant | Submitted model identity and regulatory repairability score |
| [iFixit smartphone scores](https://fr.ifixit.com/reparabilite/indices-smartphone) | 155 | Model identity, release year and independent teardown score |
| Apple and Samsung product reports | 37 configurations | Report-specific cradle-to-grave carbon footprint; production share only when explicitly reported |
| [Open Repair Data](https://openrepair.org/open-data/downloads/) | 38,677 mapped events | Aggregated brand/category repair outcomes, product age and end-of-life barrier—not model identity |
| [Our World in Data / Ember](https://ourworldindata.org/grapher/carbon-intensity-electricity) | 238 latest regional profiles | Lifecycle carbon intensity of electricity in gCO₂e/kWh |

Normalized catalogue categories:

| Category | Records | Category | Records |
|---|---:|---|---:|
| Laptop | 4,629 | Monitor | 2,710 |
| Printer / scanner | 2,632 | Tablet | 1,271 |
| Smartphone | 1,154 | Desktop | 452 |
| Television | 180 | Router / network | 93 |
| Smartwatch | 3 | Speaker | 2 |
| Streaming device | 2 | Headphones | 1 |
| Spatial computer | 1 |  |  |

Some categories have many regulatory records while others only have manufacturer-report examples. The UI exposes those coverage differences instead of filling the catalogue with invented models. Camera and game-console scenarios are supported by the model and Open Repair profiles, but no model-level source is bundled yet.

### Provenance and entity resolution

Every catalogue row includes:

- a stable source-specific `product_id` and cross-source `entity_key`;
- original source name, URL, type, licence/terms and retrieval timestamp;
- observed-field flags for energy, repairability, carbon, battery, durability and software support;
- normalized manufacturer/model identity, evidence-source count and duplicate-group size;
- freshness and evidence-quality labels;
- explicit assumptions such as the 3.85 V conversion used when EPREL publishes battery capacity only in mAh.

Entity resolution groups complementary records but does not silently merge conflicting measurements. `is_primary_record` selects the strongest representative for search; all underlying evidence remains downloadable.

## Architecture

```text
ENERGY STAR ─┐
EU EPREL ────┤
France/iFixit├── sync + normalize + resolve ── official_gadgets.csv ── SQLite/FTS5
Product PDFs ┤                  │                         │                │
Open Repair ─┤                  ├─ repair_profiles.csv    │                ├─ FastAPI
OWID/Ember ──┘                  └─ grid_intensity.csv     │                └─ Streamlit UI
                                                        │
                         balanced scenario generator ◄──┘
                                      │
                          global MLP (128 · 64 · 32)
                                      +
                     category residual MLPs (64 · 32)
                                      │
                    72% ledger + 28% neural prediction
                                      │
                score · range · factors · explanation · PDF
```

### Scoring boundary

The modeled impact burden blends:

- **72% transparent ledger**: manufacturing/materials 35%, energy 22%, lifespan/repair 18%, circularity/e-waste 13%, battery 8%, transport 4%.
- **28% neural prediction**: nonlinear estimate using category, embodied carbon, power, daily use, country grid intensity, lifespan, repairability, recyclability, recycled content, battery, weight and transport.

The final eco score is `100 − impact burden`. Higher is greener. Manufacturer-reported lifecycle carbon is displayed when available; otherwise carbon is a scenario computed from manufacturing, electricity and transport assumptions.

### Deep-learning/model approach

There is no large, current, public dataset containing complete and comparable lifecycle labels for every gadget. Training therefore uses **16,000 reproducible physics-informed scenarios**:

1. sample each category evenly;
2. anchor available features to the current official-product distributions;
3. use declared category baselines where no model-level source exists;
4. perturb use, life, grid, freight and material assumptions within documented ranges;
5. train one global multi-layer perceptron and per-category residual MLPs;
6. choose category residual weights on validation data;
7. calibrate the displayed model range using the 90th-percentile absolute error on untouched holdout scenarios.

Current held-out scenario metrics:

- 3,200 holdout scenarios
- MAE: **2.149 impact-score points**
- 90th-percentile absolute error: **4.436 points**
- R²: **0.956**

These numbers measure recovery of the disclosed synthetic target, **not accuracy against unknown real-world LCAs**. Each category's metric and selected residual weight is exposed in the UI, `models/metrics.json` and `/v1/data-quality`.

## Quick start

Python 3.10+ is recommended.

```bash
git clone https://github.com/AryaPriyanshu/environmental-impact-analyzer.git
cd environmental-impact-analyzer

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

streamlit run app.py
```

Windows PowerShell activation:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
streamlit run app.py
```

The committed data snapshot and trained model allow the UI to run offline after installation.

## Refresh, train and verify

```bash
# Fetch current public data, normalize fields and resolve entities
python src/sync_official_data.py

# Train global and category residual neural models
python src/train_model.py

# Build the indexed API catalogue
python src/database.py

# Run data freshness, snapshot alignment and distribution-drift checks
python src/monitor.py

# Verify calculations, exports, API, model and source integrity
python -m pytest -q
python -m compileall -q app.py api.py src tests
```

The refresh uses public endpoints without a required secret. An officially issued EPREL bulk key can be passed as `EPREL_API_KEY`; never commit it. Copy `.env.example` for the supported environment variables.

The scheduled workflow in `.github/workflows/refresh-data.yml` runs monthly, rebuilds all derived assets, tests them, uploads a snapshot artifact and opens a reviewable pull request. A failed source or test leaves the existing committed snapshot untouched.

## API

Start the service:

```bash
uvicorn api:app --reload --port 8000
```

Interactive docs are available at `http://localhost:8000/docs`.

```bash
# Health and snapshot identity
curl http://localhost:8000/health

# Search primary gadget records
curl "http://localhost:8000/v1/gadgets?q=ThinkPad&category=Laptop&limit=10"

# Run a custom regional scenario
curl -X POST http://localhost:8000/v1/assess \
  -H "Content-Type: application/json" \
  -d '{"name":"Scenario phone","category":"Smartphone","daily_hours":4,"grid_kg_co2_per_kwh":0.7054,"lifespan_years":5}'

# Prometheus-compatible process/request metrics
curl http://localhost:8000/metrics
```

Endpoints:

- `GET /health`
- `GET /v1/categories`
- `GET /v1/gadgets`
- `GET /v1/gadgets/{product_id}`
- `POST /v1/assess`
- `GET /v1/data-quality`
- `GET /metrics`

## Containers and deployment

Run the UI and API together:

```bash
docker compose up --build
```

- UI: `http://localhost:8501`
- API/docs: `http://localhost:8000/docs`

`Dockerfile` runs as a non-root user and defines a health check. `render.yaml` contains separate UI and API services for one-click Render deployment. Any container platform can use the same commands:

```bash
streamlit run app.py --server.address=0.0.0.0 --server.port=$PORT
uvicorn api:app --host 0.0.0.0 --port=$PORT
```

Restrict browser access to the API with `ALLOWED_ORIGINS`. No application secret is bundled.

## Repository layout

```text
app.py                              responsive Streamlit product UI
api.py                              FastAPI search and assessment service
data/official_gadgets.csv           provenance-rich normalized snapshot
data/catalog.db                     indexed SQLite/FTS5 catalogue
data/grid_intensity.csv             latest country/region electricity profiles
data/repair_profiles.csv            Open Repair brand/category aggregates
data/source_metadata.json           schema, snapshot, source and quality report
data/health_report.json             generated monitoring result
data/gadget_training_scenarios.csv  reproducible balanced model scenarios
models/gadget_impact_pipeline.joblib global + category residual neural models
models/metrics.json                 holdout calibration and drift baseline
src/sync_official_data.py           source adapters, normalization and resolution
src/train_model.py                  scenario generation, training and evaluation
src/modeling.py                     serializable category-aware model wrapper
src/utils.py                        ledger, confidence, ranges and recommendations
src/database.py                     SQLite build and repository
src/reporting.py                    branded PDF generation
src/monitor.py                      freshness/snapshot/drift health checks
tests/                              lifecycle, data, model, API and PDF tests
.github/workflows/                  CI and monthly reviewable data refresh
```

## Limitations and responsible use

- Public coverage is uneven. A category with one manufacturer report does not represent the whole market.
- ENERGY STAR and EPREL records are certification/registry evidence, not full product LCAs.
- Manufacturer footprints use configuration-specific boundaries and geography and may not be directly comparable.
- Open Repair model identity was intentionally removed upstream because of quality concerns; this project only uses its brand/category aggregates.
- mAh-to-Wh conversion, freight, materials, recycling and lifespan assumptions can dominate incomplete records. They are visible and editable.
- Regional annual-average electricity intensity does not represent every local supplier, hour or marginal-generation effect.
- Confidence is an evidence-coverage indicator, not a statistical probability.
- The model is interpolation over a disclosed scenario system, not a substitute for EPDs or supplier-specific primary data.
- Results are educational decision support, not ISO 14040/14044-conformant LCAs, procurement declarations or regulatory disclosures.

## Licence

Project code is licensed under the repository `LICENSE`. Data remains subject to each linked source's licence and terms; those are retained in row-level fields and `data/source_metadata.json`.

## Author

Priyanshu Arya · [LinkedIn](https://www.linkedin.com/in/priyanshu-arya-408a10268/)
