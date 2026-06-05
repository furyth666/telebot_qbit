# PLAN

## Goal

Finalize newly added torrents by identifying JAV first; only non-JAV torrents should ask for manual category selection.

## Tasks

1. Keep Jellyfin duplicate deletion as the first post-add guard.
2. If the torrent name contains a valid JAV code, check Jellyfin for the same code before categorizing.
3. If Jellyfin already has the same code, delete only when duplicate deletion is enabled and the 4K policy allows it.
4. Keep a new 4K torrent when Jellyfin only has a non-4K copy; delete it only when Jellyfin also has a 4K copy.
5. If the JAV torrent is retained, create/set the JAV category, apply large-file filtering, record the processed hash, and notify Telegram.
6. If the torrent is not JAV, show the existing category selection buttons.

## Verification

- `python -m unittest discover -s tests -v`
- `python -m py_compile app/*.py tests/*.py`
- `git diff --check`
