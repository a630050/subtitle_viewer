# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run locally:**
```bash
# Activate virtualenv first (Windows)
.venv\Scripts\activate

# Run with Flask dev server (no WebSocket support in debug mode)
python app.py

# Run with gunicorn (matches production, requires Unix/WSL)
gunicorn -k eventlet -w 1 --bind 0.0.0.0:8080 app:app
```

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Deploy to Fly.io:**
```bash
fly deploy
```

## Architecture

This is a single-file Flask + Socket.IO real-time collaborative subtitle editor, deployed on Fly.io.

**Key components:**

- `app.py` — entire backend: Flask routes, Socket.IO event handlers, `ScriptManager` class, room management, and a cleanup daemon thread
- `templates/index.html` — director/editor UI (not visible in file listing but referenced by Flask)
- `templates/viewer.html` — audience/viewer UI; receives live subtitle updates via WebSocket

**Room model:**
- `rooms` dict (keyed by `director_id`) holds a `ScriptManager` instance plus sets of connected Socket.IO SIDs for directors and viewers
- `viewer_to_room` dict maps `viewer_id` → `director_id`, creating separate shareable URLs for audiences
- All state (text, settings, speech lock) lives in memory — no database; rooms expire after 24 hours of inactivity

**Real-time sync strategy:**
- Text edits are sent as diff-match-patch patches (`patch_script` event) to minimize payload; on patch failure the server forces a full `state_update` to re-sync
- Speech recognition lock (`speech_user`) is per-room; only one SID can hold it at a time
- Director settings and viewer settings are stored separately in `ScriptManager`; viewer settings are broadcast to all room members while director settings go only to directors

**Concurrency:** gunicorn runs with `eventlet` worker (`-w 1`) — a single worker is required for Socket.IO's in-process room state to work correctly. The `threading.Lock` (`lock`) guards the `rooms` dict.

**Environment:**
- `SECRET_KEY` is read from env var (falls back to hardcoded default for local dev)
- Port 8080 inside container, mapped to 80/443 externally via Fly.io
