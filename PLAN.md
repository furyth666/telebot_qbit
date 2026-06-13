# PLAN

## Goal

Standardize the Qbit_bot codebase without changing live bot behavior.

The repo should stay a practical personal qBittorrent Telegram bot, not become a
framework. Refactors must keep the existing category prompt flow, qBittorrent auth
fallbacks, Telegram network recovery behavior, and unRAID deployment path intact.

## Current Baseline

- `python -m unittest discover -s tests -v` passes: 59 tests.
- `python -m py_compile app/*.py tests/*.py` passes.
- `git diff --check` passes.

## Phase 1: Module Interface Standardization

1. Rename cross-module helper imports away from leading-underscore names. `app.formatters` now exposes public message-formatting helpers for handlers, jobs, and callback actions. `app.jav_rules` now exposes public rule functions for JAV code extraction, lookup parsing, title checks, and add-context matching.
2. Keep private names only for helpers that are truly internal to their module.
3. Remove temporary backwards-compatible aliases after callers and tests use public names. `app.handler_utils`, `app.formatters`, `app.jav_rules`, `app.runtime_state`, and workflow modules now advertise only their public interfaces.
4. Add explicit `__all__` lists to the standardized modules so public interfaces are visible and compatibility aliases stay out of the advertised surface.
5. Add or adjust focused tests only when a rename changes a real caller surface.

## Phase 2: Handler and Formatting Locality

1. Keep Telegram command handlers thin: authorization, argument parsing, reply dispatch.
2. Move reusable qBittorrent action behavior behind a small handler-facing interface. `app.callback_actions` now owns torrent detail rendering and callback button actions.
3. Keep formatting behavior centralized, but expose only message-level formatters to handlers. Callback payload construction/parsing now goes through `app.callback_data`.
4. Add callback tests for category selection, stale buttons, delete actions, detail refresh, and qBittorrent action errors.

## Phase 3: Add and Finalize Flow

1. Treat "add links -> locate new torrent -> prompt or auto-apply category" as one workflow module. `app.add_flow` now owns add-link submission and background finalize task scheduling.
2. Keep qBittorrent polling and Telegram messaging decisions close to the workflow.
3. Preserve the compact callback payload shape: `tor:cat:all:<hash>:<index>`.
4. Expand tests around duplicate prompt suppression and fallback to manual classification.

## Phase 4: JAV Post-Add Policy

1. Keep JAV category creation, qBittorrent category assignment, file priority filtering, and processed-state persistence behind one module. `app.jav_policy` now owns the shared category/file-selection policy used by post-add handling and `/retryjav`.
2. Move Jellyfin duplicate policy into the same module once its delete/grace-window/4K exception cases have focused tests. `app.jav_policy` now returns structured duplicate outcomes for delete, grace-window, 4K exception, and keep cases.
3. Keep Telegram message rendering outside the policy module so handlers and jobs can present context-specific wording.

## Phase 5: Runtime State and Lifecycle

1. Make bot runtime state keys explicit in one module instead of scattered string literals. `RuntimeContext` is now the standard interface over `Application.bot_data`.
2. Keep persistent state access behind the state store interface.
3. Keep watchdog behavior unchanged: Telegram failures can stop the bot; qBittorrent failures keep webhook alive.
4. Add tests for startup state initialization and completion notification persistence. `tests/test_lifecycle.py` now covers `post_init` success and qBittorrent-baseline failure paths.
5. Keep state persistence resources scoped. `StateStore` now closes SQLite connections after load/save instead of relying on transaction context managers.
6. Keep runtime compatibility aliases out of the advertised public interface. `RuntimeContext` exports are covered by `tests/test_runtime_state.py`.

## Phase 6: External Adapters

1. Keep qBittorrent, Jellyfin, LLM, and Telegram as explicit adapters.
2. Do not introduce abstract seams unless there are at least two real adapters or tests need the seam.
3. Keep `trust_env=False` for LAN qBittorrent/Jellyfin clients and LLM calls. `tests/test_adapters.py` now covers Jellyfin, OpenAI-compatible LLM, and local Ollama request construction.
4. Preserve qBittorrent Bearer token preference and username/password fallback.
5. Add explicit `__all__` lists to adapter modules so their public data types and client classes are discoverable.

## Verification

- `python -m unittest discover -s tests -v`
- `python -m py_compile app/*.py tests/*.py`
- `git diff --check`
- `bash scripts/validate.sh`
- Expected failure-path logs are captured inside tests so validation output stays reserved for real failures.

## Deployment Checkpoint

For behavior-changing phases, deploy and verify the live unRAID bot:

- `./scripts/sync_unraid.sh`
- container is healthy
- port `8099` is listening on `0.0.0.0`
- public Cloudflare/Tornado probe returns expected `404`
- Telegram webhook info is `ok=True`
- `pending_update_count=0`
