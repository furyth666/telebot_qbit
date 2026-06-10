# PLAN

## Goal

Finalize newly added torrents by letting an LLM choose the qBittorrent category.
The bot still validates the decision before applying it.

## Tasks

1. Add LLM classification settings without making LLM credentials required.
2. Add an OpenAI-compatible classifier that returns structured category decisions.
3. Collect torrent name, size, current category, available categories, and file metadata for the classifier.
4. Apply the LLM category only when it is enabled, confident enough, and matches an existing qBittorrent category.
5. Fall back to the existing manual category prompt when the LLM is disabled, unavailable, low-confidence, or returns an invalid category.
6. Notify Telegram when a category was applied automatically, while keeping manual correction buttons available.

## Verification

- `python -m unittest discover -s tests -v`
- `python -m py_compile app/*.py tests/*.py`
- `git diff --check`
