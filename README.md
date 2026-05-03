# DB Studio — AI Database Platform

Production-grade README for the DB Studio project. This document focuses on the implemented system, operational guidance, and developer touchpoints.

TL;DR: `DB Studio` is an agent-based toolkit to design, modify, and query SQLite databases via a Streamlit UI. The system uses Azure OpenAI for NL→SQL tasks and Azure Blob Storage for durable workspace and database storage.

## Quick links
- Run locally: `streamlit run app.py`
- Config: `shared/config.py`
- Workspace model: `shared/workspace.py`
- Feature 3 entry: `Features/Feature3_chat_db/__init__.py`

## Production-ready overview
This repository provides:
- a modular feature-based architecture (`Features/`) where each feature encapsulates agents, state, and utilities
- a single Streamlit-based UI surface (`app.py` + `feature*_app.py`) for developer and end-user interactions
- centralized configuration via `pydantic` in `shared/config.py` with environment-driven overrides
- durable persistence of `Workspace` state and `.db` artifacts in Azure Blob Storage, with transient local SQLite files used for execution

## Project structure (concise)
Root files and important folders:

- `app.py` — Streamlit entry and router
- `feature1_app.py`, `feature2_app.py`, `feature3_app.py` — UI modules
- `Features/` — feature implementations (each feature has agents, utils, and state)
- `shared/` — `config.py`, `blob_storage.py`, `workspace.py`, `cache.py`, `sidebar.py` and helpers
- `requirements.txt` — pinned runtime dependencies

Detailed subfolders (high level):
- `Features/Feature1_create_db/` — schema design, validators, suggestion agents
- `Features/Feature2_modify_db/` — modifiers, validators, execution helpers
- `Features/Feature3_chat_db/` — NL→SQL agent graph, execution flow, trace utilities

## System flow (end-to-end)
1. UI (Streamlit) receives input and updates the `Workspace` model (`shared/workspace.py`).
2. `Workspace` is persisted to blob storage using `shared/blob_storage.save_workspace`.
3. For NL queries, `feature3_app` calls `run_feature_3(workspace)`.
4. `run_feature_3_pipeline` builds a `DBDesignerState`, runs `run_query_agent` (agent graph in `Queryagent.py`) to generate SQL or clarification.
5. If approved, the SQL executes via `execution/executor.py` against a local temp SQLite DB (downloaded from blob storage if needed).
6. Results and NL summary from `generate_nl_response` are written back to `workspace.feature3_data` and rendered in the UI.

## Configuration (environment variables)
Primary variables (see `shared/config.py` for aliases and additional tuning):

- `AZURE_OPENAI_ENDPOINT` — required for Azure OpenAI access
- `AZURE_OPENAI_API_KEY` — required API key
- `AZURE_OPENAI_API_VERSION` — default `2024-05-01-preview`
- `AZURE_OPENAI_DEPLOYMENT` — chat model deployment name
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` — embedding model deployment name
- `AZURE_STORAGE_CONNECTION_STRING` or (`AZURE_STORAGE_ACCOUNT_NAME` + `AZURE_STORAGE_ACCOUNT_KEY`) or (`AZURE_STORAGE_ACCOUNT_URL` + `AZURE_BLOB_SAS_TOKEN`) — blob credentials
- `BLOB_CONTAINER_ACTIVE`, `WORKSPACE_CONTAINER` — container names

Use a `.env` populated with these keys for local development; `shared/config.py` loads `.env` via `pydantic-settings` and `dotenv`.

## Security & operational notes
- Secrets must never be committed. Use a secrets manager or CI/CD environment variables for production deployments.
- The app creates temporary local copies of `.db` artifacts for execution. Ensure disk cleanup policies or ephemeral container runtimes for production.
- Some dependencies pull telemetry (e.g., `langsmith` transitively). Review `requirements.txt` if telemetry must be avoided.

## Running locally (quickstart)
1. Create and activate a virtual environment (Python 3.13 recommended):
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
2. Add a `.env` with the Azure settings (see Configuration above).
3. Start the UI:
```bash
streamlit run app.py
```

## Developer tasks and where to edit
- Add/modify LLM behavior: `Features/*/services/` or `shared/config.py`.
- Modify workspace model: `shared/workspace.py`.
- Change blob handling: `shared/blob_storage.py`.
- Extend Feature 3 agent graph: `Features/Feature3_chat_db/Queryagent.py`, `nodes/`, `core/`.

## Testing suggestions
- Add unit tests for:
  - `Features/Feature3_chat_db/_build_query_state` and `_write_back` logic
  - `shared/blob_storage` upload/download with a local Azurite mock or mocking library
  - agent node functions in `Features/Feature3_chat_db/nodes`
- Add a small sample `.db` and an integration test that runs a short NL→SQL→execute cycle.

## Deployment notes
- For production run, serve the Streamlit app behind a reverse proxy (HTTPS) and run in an isolated container.
- Use managed secrets and a CI job that injects required env vars; avoid putting keys in `.env` in source control.

## Contributing & next steps
- If you want, I can add:
  - an `.env.example` built from the keys referenced in `shared/config.py`
  - a `DEV.md` with reproducible developer commands
  - a `CONTRIBUTING.md` describing PR workflow and code style

## Contact
Open an issue or PR in this repository for feature requests, bugs, or deployment questions.

---
This README strictly documents the code present in this repository; it avoids describing unimplemented or speculative features.

## Project Structure (detailed)
Below is the repository layout emphasizing the modules you'll interact with while developing or reviewing features.

Root
- app.py — Streamlit entry and UI router
- feature1_app.py, feature2_app.py, feature3_app.py — top-level UI modules
- requirements.txt
- README.md
- orchestrator/
  - router.py
- Features/
  - Feature1_create_db/
    - models.py
    - validators.py
    - agents/ (query_generator.py, requirement_analyzer.py, schema_designer.py, suggestion_agent.py, validation_agent.py)
    - memory/ (session_store.py)
    - services/ (llm_service.py, orchestrator.py)
    - utils/ (erd_visualizer.py, report_generator.py)
  - Feature2_modify_db/
    - config.py, graph.py, state.py
    - agents/ (clarifier.py, executor.py, modifier.py, validator.py)
    - utils/ (blob_storage.py, db_utils.py, erd_data.py, erd_renderer.py, file_import.py, memory.py, pdf_report.py)
  - Feature3_chat_db/
    - __init__.py (entrypoints: run_feature_3, pipeline/execute functions)
    - Queryagent.py
    - state.py
    - core/ (identifier_normalizer.py, runtime_db.py, schema_builder.py, sql_utils.py, sql_validation.py, validation_engine.py)
    - execution/ (executor.py)
    - graph/ (builder.py, router.py)
    - nodes/ (generate_sql.py, intent_router.py)
    - observability/ (tracing.py)
    - prompts/ (templates.py)
    - retrieval/ (hybrid_retriever.py, pattern_library.py)
    - utils/ (validation_utils.py)
- shared/
  - config.py (pydantic settings + `get_chat_llm`/`get_embeddings` helpers)
  - blob_storage.py (save/load/upload/download helpers for Azure Blobs)
  - workspace.py (Pydantic `Workspace` model and `WorkspaceState` enum)
  - cache.py, sidebar.py, import_paths.py, db_utils.py, pdf_report.py
  - erd/ (data.py, renderer.py)
- output/ (generated artifacts)

This structure is deliberately modular: feature folders contain agents, utilities, and state for each capability, while `shared/` centralizes cross-cutting services and configuration.

## Feature Details (expanded)

- Feature 1 — Database creation
  - Key responsibilities: requirement analysis, schema suggestion, iterative validation, and producing a `.db` artifact plus a report/ERD.
  - Notable code: `Features/Feature1_create_db/models.py`, `validators.py`, `agents/*`, `services/llm_service.py`.

- Feature 2 — Database modification
  - Key responsibilities: apply safe modifications to existing DBs with validation and optional manual approval.
  - Notable code: `Features/Feature2_modify_db/agents/*`, `utils/db_utils.py`, `state.py`.

- Feature 3 — NL→SQL chat and execution
  - Key responsibilities: accept NL queries, generate SQL (via agent graph), optionally clarify, ask for approval for modifying queries, execute on a local SQLite file, and produce NL summaries and structured results.
  - Entry points: `Features/Feature3_chat_db/__init__.py` (`run_feature_3`, `run_feature_3_pipeline`, `run_feature_3_execute`).
  - Agent graph: implemented in `Features/Feature3_chat_db/Queryagent.py` and supporting `core/` modules.

## Where to Look for Common Tasks
- Add a new agent or node: `Features/<feature>/agents/` or `Features/Feature3_chat_db/nodes/`.
- Change workspace schema or persisted fields: `shared/workspace.py`.
- Adjust LLM settings or environment mapping: `shared/config.py`.
- Change blob behavior: `shared/blob_storage.py`.

## Short Examples (end-to-end)
- Start app and open UI:
```bash
streamlit run app.py
```
- Example chat query (Feature 3):
  - Enter: "Find customers who rented from both stores"
  - The pipeline: UI → `run_feature_3_pipeline` → `run_query_agent` → (clarify/approve?) → `execute_query_result` → `generate_nl_response` → workspace updated with `feature3_data` (SQL, rows, nl_response, trace).

## Final notes
- The README now includes the repository structure and more granular pointers to where features and agents live. If you want, I can also:
  - add a small `DEV.md` with common dev commands, or
  - create an `.env.example` file populated from `shared/config.py` aliases.
