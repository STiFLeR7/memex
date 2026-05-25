# spec: Debouncing Watcher Notification Bursts

## Goal
Optimize `notify_local_server` to debounce rapid consecutive file change notifications (e.g. from branch switching or bulk file edits), preventing the watcher from spawning excessive concurrent threads and sending redundant HTTP requests.

## Proposed Architecture

### 1. Global Debounce State (`memex/watcher/handlers.py`)
- **Variables**:
  - `_notify_timer: Optional[threading.Timer] = None`
  - `_notify_lock = threading.Lock()`
- **Purpose**: Maintain a single active timer across the watcher process threads and serialize access to it.

### 2. Timer-Based Coalescing
- **Mechanism**:
  - Acquire `_notify_lock`.
  - Cancel any active `_notify_timer`.
  - Schedule a new `threading.Timer` with a `500ms` delay.
  - Upon timer execution:
    1. Reset `_notify_timer` to `None`.
    2. Read the active port from `.memex/port`.
    3. Dispatch the single `POST /notify` request.

## Verification Plan

### Automated Tests
- Add a unit test verifying that calling `notify_local_server()` 5 times in rapid succession only results in a single HTTP request after the 500ms quiet window has passed.

### Manual Verification
1. Open the memex server.
2. Modify several files rapidly (e.g. run a script modifying 10 files).
3. Verify that only a single `POST /notify` request is logged in the server console, and the VS Code Webview updates exactly once.
