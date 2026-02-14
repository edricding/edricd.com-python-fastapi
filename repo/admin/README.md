# Admin API Reference

This document describes the backend APIs used by the admin pages under `repo/admin`.

Source of truth: `repo/backend/app/main.py`  
Last updated: 2026-02-14

## Base URL

- Local: `http://localhost:8000`
- Production example: `https://dashboard.edricd.com`

## Auth Model

- Session cookie name: `edricd_session`
- Most admin APIs require a valid session cookie.
- Unauthorized response is usually:

```json
{
  "success": false,
  "message": "unauthorized"
}
```

- `GET /api/session/require` is special:
  - `204` if session is valid
  - `401` if session is invalid

## API Index

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/api/health` | No | Health check |
| GET | `/api/recaptcha-sitekey` | No | Get reCAPTCHA site key and login requirement |
| POST | `/api/AuthLogin` | No | Login and set session cookie |
| POST | `/api/AuthLogout` | No | Logout and clear session cookie |
| GET | `/api/session/status` | No | Check current login state |
| GET | `/api/session/require` | Session | Validate session (204/401) |
| GET | `/api/reminder/schedule` | Session | Load schedule + audio + presets |
| POST | `/api/reminder/slot/save` | Session | Create or update slot |
| POST | `/api/reminder/slot/delete` | Session | Delete slot |
| GET | `/api/reminder/preset/list` | Session | List presets |
| POST | `/api/reminder/preset/save` | Session | Create or update preset |
| POST | `/api/reminder/preset/delete` | Session | Delete preset |
| GET | `/api/reminder/audio/list` | Session | List audio library |
| POST | `/api/reminder/audio/save` | Session | Create or update audio |
| POST | `/api/reminder/audio/delete` | Session | Delete audio |
| GET | `/api/reminder/current` | Session | Get current/next slot |
| GET | `/api/reminder/device/current` | No | Device polling API with first-time trigger |
| GET | `/api/users` | Session | List users |
| POST | `/api/users/create` | No | Create user |
| POST | `/api/contact` | No | Contact form submission |

## Auth APIs

### `GET /api/recaptcha-sitekey`

Response:

```json
{
  "site_key": "xxxx",
  "login_required": false
}
```

### `POST /api/AuthLogin`

Request body:

```json
{
  "username": "admin",
  "password": "your-password",
  "recaptchaToken": "optional-token"
}
```

Success response:

```json
{
  "success": true,
  "message": "Login success",
  "username": "admin",
  "expiresAt": 1739520000
}
```

### `POST /api/AuthLogout`

Success response:

```json
{
  "success": true,
  "message": "Logout success"
}
```

### `GET /api/session/status`

Logged-in response:

```json
{
  "loggedIn": true,
  "username": "admin",
  "expiresAt": 1739520000
}
```

Logged-out response:

```json
{
  "loggedIn": false
}
```

## Reminder APIs

### `GET /api/reminder/schedule`

Returns:

- `timezone`
- `slots[]`
- `audios[]`
- `presets[]`

### `POST /api/reminder/slot/save`

Request body:

```json
{
  "id": 1,
  "weekday": 1,
  "start_min": 540,
  "end_min": 600,
  "title": "Focus",
  "note": "Optional",
  "audio_id": 2,
  "color": "bg-primary",
  "is_enabled": true,
  "sort_order": 1
}
```

If `id` is omitted, a new slot is created.

### `POST /api/reminder/slot/delete`

```json
{
  "id": 1
}
```

### `GET /api/reminder/preset/list`

Returns preset list for the settings page.

### `POST /api/reminder/preset/save`

Request body:

```json
{
  "id": 3,
  "name": "Short Break",
  "duration_min": 15,
  "audio_id": 2,
  "color": "bg-info",
  "is_enabled": true,
  "sort_order": 2
}
```

If `id` is omitted, a new preset is created.

### `POST /api/reminder/preset/delete`

```json
{
  "id": 3
}
```

### `GET /api/reminder/audio/list`

Returns audio library entries:

- `id`
- `name`
- `gcs_url`
- `mime_type`
- `duration_seconds`
- `is_active`

### `POST /api/reminder/audio/save`

Request body:

```json
{
  "id": 5,
  "name": "Bell",
  "gcs_url": "https://storage.googleapis.com/your-bucket/bell.mp3",
  "mime_type": "audio/mpeg",
  "duration_seconds": 3,
  "is_active": true
}
```

Notes:

- If `id` is omitted, a new row is created.
- `gcs_url` max length is 1024.
- Duplicate `gcs_url` returns `audio URL already exists`.

### `POST /api/reminder/audio/delete`

```json
{
  "id": 5
}
```

Note: when deleting audio, related `reminder_preset.audio_id` is set to `NULL`.

### `GET /api/reminder/current`

Returns:

- `timezone`
- `server_now`
- `weekday`
- `minute_of_day`
- `hhmm`
- `current_slot` (or `null`)
- `next_slot`
- `minutes_until_next`

### `GET /api/reminder/device/current`

Purpose: polling endpoint for hardware speaker/alarm device.

Device identity:

- Query: `device_id=...`, or
- Header: `X-Device-Id: ...`, or
- Fallback: `default-device`

Response:

```json
{
  "success": true,
  "message": "ok",
  "data": {
    "device_id": "device-001",
    "timezone": "Asia/Shanghai",
    "server_now": "2026-02-14T12:34:56+08:00",
    "event": {
      "id": 12,
      "name": "Lunch",
      "audio_url": "https://storage.googleapis.com/your-bucket/lunch.mp3",
      "weekday": 6,
      "start_min": 720,
      "end_min": 780,
      "hhmm_start": "12:00",
      "hhmm_end": "13:00"
    },
    "is_first_time": true
  }
}
```

Behavior:

- If no current event: `event = null`, `is_first_time = false`.
- On first poll inside an event window for a given device: `is_first_time = true`.
- Later polls in the same window: `is_first_time = false`.
- Current implementation stores first-time state in process memory, so it resets after service restart.

## User APIs

### `GET /api/users`

Returns:

```json
{
  "success": true,
  "message": "ok",
  "columns": ["id", "username", "last_login_time"],
  "data": []
}
```

### `POST /api/users/create`

Request body:

```json
{
  "username": "new-user",
  "password": "plain-password"
}
```

## Error Notes

- Validation errors from FastAPI return HTTP `422`.
- Business errors return `success: false` with a `message`.
- DB/network issues are returned in `message` as `... failed: <reason>`.
