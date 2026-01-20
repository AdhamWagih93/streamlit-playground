# AI Assistant Instructions for this Repo

## Project Overview
- Multi-page Streamlit app: landing page in [app.py](../app.py); feature pages in [pages](../pages) (team task manager, DevOps referral agent, WFH schedule 2026).
- Shared utilities live under [src](../src): theming ([src/theme.py](../src/theme.py)), auth helpers ([src/auth.py](../src/auth.py)), task persistence ([src/tasks_repo.py](../src/tasks_repo.py)), resume parsing ([src/resume_parser.py](../src/resume_parser.py)), and small helpers ([src/utils.py](../src/utils.py)).
- Long-lived data is kept under [data](../data): SQLite DB (tasks), WFH JSON schedules/holidays, interview scorecards CSV, and example holiday/task JSON files.

## How to Run & Debug
- Use Streamlit from the project root:
  - `python -m streamlit run .\app.py`
- Auto-reload is enabled via [.streamlit/config.toml](../.streamlit/config.toml) (watchdog file watcher). Prefer saving files and letting Streamlit reload instead of adding manual reload logic.
- On Windows, PostgreSQL support is optional; by default tasks use SQLite at `data/tasks.db` via SQLAlchemy.

## Architecture & Data Flow
- **Home page** ([app.py](../app.py)) only handles branding/landing UI and calls `set_theme` from [src/theme.py](../src/theme.py).
- **Team Task Manager** ([pages/1_Team_Task_Manager.py](../pages/1_Team_Task_Manager.py))
  - Uses `tasks_repo` as the single source of truth for tasks; session state ([st.session_state]) holds a cached copy (`tasks_cache`) for UI filtering only.
  - Task lifecycle (create/update/delete, status moves, comments, checklist) must go through [src/tasks_repo.py](../src/tasks_repo.py) so history, JSON-encoded fields, and migrations stay consistent.
  - Database backend:
    - Default: SQLite file under [data](../data) (configured in `get_engine` in [src/tasks_repo.py](../src/tasks_repo.py)).
    - Override via `DATABASE_URL` for PostgreSQL; keep new SQL compatible with both backends.
- **DevOps Referral Agent** ([pages/2_DevOps_Referral_Agent.py](../pages/2_DevOps_Referral_Agent.py))
  - Parsing APIs are `extract_text_from_file`, `extract_text_from_path`, `parse_resume`, and `resume_profile_to_dict` in [src/resume_parser.py](../src/resume_parser.py); treat these as the main interface for resume processing.
  - Interview scorecards are stored in [data/interview_scorecards.csv](../data/interview_scorecards.csv); updates append/replace by `candidate` key. Preserve this schema if you extend it.
  - Local folder scan path is currently hardcoded (`D:\\DevOps CVs\\12-2025\\HR`). If you change it, keep it configurable and clearly labeled as a local-only feature.
- **WFH Schedule 2026** ([pages/3_WFH_Schedule.py](../pages/3_WFH_Schedule.py))
  - Core invariants are enforced by `validate_schedule` (workload, office count, role coverage, 2-week rules). Any change to generation or editing must still pass these validations.
  - Generated base pattern → full-year schedule → persisted per-week JSON under [data/wfh_schedules/2026](../data/wfh_schedules/2026). Manual edits operate on these week files via `load_week` / `save_week`.
  - Holidays use two stores: personal holidays ([data/holidays_2026.json](../data/holidays_2026.json)) and public holidays ([data/public_holidays_2026.json](../data/public_holidays_2026.json)); keep them as `{date_iso: [...]}` / `{date_iso: name}` mappings.

## Key Patterns & Conventions
- **Theming**: call `set_theme(...)` from [src/theme.py](../src/theme.py) once near the top of each new page for consistent page config and CSS. Do not duplicate `st.set_page_config` logic inline unless you have a clear reason (e.g., WFH page does its own config).
- **State management**: use `st.session_state` keys established in the page modules (e.g., `tasks_cache`, `users`, `teams`, `selected_team`, `devops_*` keys) and extend them in-place instead of inventing parallel structures.
- **Task mutations**:
  - Always go through repository functions in [src/tasks_repo.py](../src/tasks_repo.py): `create_task`, `update_task`, `append_history`, `update_task_status`, checklist helpers, etc.
  - `Task.history`, `Task.comments`, `Task.tags`, and `Task.checklist` are JSON-encoded text columns. When adding new fields, update both the SQLAlchemy model and the lightweight migration in `init_db`.
  - `DEFAULT_TEAM` and `ensure_default_team` backfill `team` on legacy rows; keep that behavior when evolving the schema.
- **Schedule mutations**:
  - For WFH, use helpers like `build_error_free_base_schedule`, `ensure_week_files`, `load_week`, `save_week`, and the existing validation pipeline; avoid ad-hoc writes to JSON without going through these helpers.

## External Dependencies & Files
- Main dependencies: Streamlit, pandas/numpy, Plotly, SQLAlchemy, pdfplumber, docx2txt (see [requirements.txt](../requirements.txt)). Use these instead of adding overlapping libraries where possible.
- Authentication in [src/auth.py](../src/auth.py) reads `VALID_USERNAME` / `VALID_PASSWORD` from environment; do not hardcode credentials into code.
- CSS is centralized in [assets/custom_theme.css](../assets/custom_theme.css); new global styles should be added there, with per-page overrides via `st.markdown(..., unsafe_allow_html=True)` when necessary.

## When Extending the App
- Prefer adding new user-facing flows as separate Streamlit pages under [pages](../pages), keeping heavy business logic in [src](../src).
- Reuse existing helpers (`tasks_to_df`, scheduling utilities, resume parsing) instead of re-implementing similar logic.
- Preserve existing data formats in [data](../data) unless you also provide a clear, idempotent migration path in code.
