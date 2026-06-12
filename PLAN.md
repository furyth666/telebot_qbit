# PLAN

## Goal

Finalize newly added torrents by letting an LLM choose the qBittorrent category.
The bot still validates the decision before applying it.

## Tasks

1. Add LLM classification settings without making LLM credentials required.
2. Add an OpenAI-compatible classifier that returns structured category decisions.
3. Collect torrent name, size, current category, available categories, and file metadata for the classifier.
4. Send the LLM recommendation with the normal manual category buttons.
5. Give the user a short confirmation window before auto-applying the recommendation.
6. Apply the LLM category only if the user does not choose a manual category before the timeout.
7. Fall back to the existing manual category prompt when the LLM is disabled, unavailable, low-confidence, or returns an invalid category.
8. Notify Telegram when a category was applied automatically.

## Verification

- `python -m unittest discover -s tests -v`
- `python -m py_compile app/*.py tests/*.py`
- `git diff --check`
