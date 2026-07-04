"""Named constants used across the application."""

from __future__ import annotations

DEFAULT_DOCKER_IMAGE = "fully-loaded"
DEFAULT_MODEL_ID = "unknown"
CONFIG_FILENAME = "config.json"
META_CACHE_FILENAME = "_meta_cache.json"

INTERRUPTED_MARKER_FILENAME = "groket-interrupted.json"  # on-disk marker

LOG_BUFFER_MAXLEN = 8000
LOG_TAIL_MAXLEN = 4000
MAX_RUN_HISTORY = 20
# Activity bar (cheap counters — not a traces poller).
ACTIVITY_BAR_INTERVAL = 5.0
# While analysis/refresh pools are busy, refresh activity bar this often (spinner).
ACTIVITY_BAR_BUSY_INTERVAL = 0.08
# Findings/report pending spinner (update Static text only — not whole tables).
ANALYSIS_PENDING_SPINNER_INTERVAL = 0.08
# Full traces-tree walk only when idle and FS events were sparse (rare).
LIVE_POLL_FULL_WALK_INTERVAL = 60.0
# Min gap between FS-triggered session list scans (debounce beyond FS watch).
LIVE_POLL_ACTIVE_INTERVAL = 1.0
# Timer interval when TraceTreeWatch cannot start (no inotify / missing root).
LIVE_POLL_WATCH_FALLBACK_INTERVAL = 5.0

DIFF_TRUNCATE_THRESHOLD = 120_000
DIFF_TRUNCATE_HEAD = 60_000
DIFF_TRUNCATE_TAIL = 40_000
INCOMPLETE_STALE_SECONDS = 20 * 60
