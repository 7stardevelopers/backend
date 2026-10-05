# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Activate venv first (macOS/Linux):**
```bash
source venv/bin/activate
pip install -r requirements.txt
```

**Run locally (two options):**
```bash
# Option 1: Lightweight Python server (reads .env directly, no SAM needed)
python run_local.py          # listens on http://localhost:8000

# Option 2: AWS SAM (closer to Lambda, requires env.json)
sam local start-api --env-vars env.json
```

**Run unit tests (no DB/Redis/AWS needed — uses in-memory SQLite):**
```bash
python -m unittest discover -s tests -t .
```

**Run integration tests (requires live DB + Redis — reads .env):**
```bash
python full_test.py
```

**Deploy:**
```bash
sam build && sam deploy --guided          # staging (first time)
sam build && sam deploy --config-env production # production
```

**Environment setup:**
Copy `.env` to the project root with all required vars (see Key env vars below). When `SECRET_NAME` is not set, `env_loader.py` skips Secrets Manager and uses `os.environ` — this is the local dev path.

## Architecture

### Single-Lambda design
Everything runs in one AWS Lambda (`lambda_function.handler`). REST vs WebSocket is distinguished by checking `event.requestContext.connectionId`. A second Lambda (`location_trigger.handler`) fires on a 15-minute EventBridge schedule to send pre-booking push notifications.

### Request flow (REST)
```
API Gateway → lambda_function.handle_rest
  → request_handler.parse_request      # merges body + query params; decodes JWT → _user_id, _role
  → routing.dispatch_rest              # regex fullmatch against ROUTES list → service method
  → <module>/<module>_service.py method(obj, connection)
```

Every route requires a valid access token except those listed in `PUBLIC_ROUTES` in `routing.py` — add new public endpoints there explicitly. `page`/`per_page` are clamped centrally.

`obj` is the merged body + query params dict, with `_user_id` and `_role` injected. Path params (e.g. `id` from `/bookings/{id}`) are also injected into `obj` by the dispatcher. Services `pop()` `_user_id`/`_role` from `obj` before passing to Pydantic validators.

**Response convention:** service methods return `("success", data)` → HTTP 200, or `("created", data)` → HTTP 201. `PermissionError` → 403 (except messages `"Token expired"`/`"Invalid token"`, which map to 401), `ValueError` → 400, `routing.RouteNotFound` → 404, uncaught exceptions → 500 (traceback logged).

### Request flow (WebSocket)
```
API Gateway WebSocket → lambda_function.handle_websocket
  → routing_wss.dispatch_wss → web_sockets/web_sockets_service.py
```
Routes: `$connect`, `$disconnect`, `sendMessage`, `locationUpdate`, `markSeen` (`markDelivered` = legacy alias), `joinBooking`, `$default`. JWT is passed as a `?token=` query param on `$connect` (no Authorization header on WebSocket).

**WebSocket API is defined in `template.yaml`** (`WebSocketApi`, one route per `routing_wss.WSS_ROUTES` key, stage = `Environment`, AutoDeploy). Every deploy creates/updates it and sets `WEBSOCKET_ENDPOINT_URL` on the API Lambda automatically. The app URL (`EXPO_PUBLIC_WSS_URL`) is the stack output `WebSocketUrl`. **When adding a WS route, add it to both `routing_wss.py` and `template.yaml`.**

### Chat
`chat/messages_service.py`. Open only while the booking is `ACCEPTED / EN_ROUTE / IN_PROGRESS` (read-only otherwise); max 1000 chars, `message_type` `text`, 20 msgs/min/user/booking (Redis). Phone numbers / emails / UPI IDs are masked (`utilities/contact_masking.py`). A send pushes WS frame `{message_type: "chat", content_type, ...message}` to **both** participants — the sender's copy carries the client's `client_id`. WS send failures push `{message_type: "chat_error", client_id, error}` to the sending socket. `markSeen` / `POST /bookings/{id}/messages/seen` / `GET …/messages?mark_seen=1` set `seen_at` and push `chat_seen` to the other party; plain GETs never mark seen. GET supports `?before=<message_id>&limit=` and `?since=<iso>`. All chat timestamps are ISO-8601 UTC with `Z` (`utilities/time_format.py`). New messages send an Expo push (`type: new_message`, not recorded in in-app notifications).

### Module structure
Every feature module follows the same three-file pattern:
- `*_modal.py` — SQLAlchemy CRUD (accesses `metadata.tables["table_name"]` via `get_table()`)
- `*_validator.py` — Pydantic v2 schemas for input validation
- `*_service.py` — business logic; instantiated once at module level in `routing.py`

### Database access
`db_connection.py` creates a single SQLAlchemy engine at cold-start and calls `metadata.reflect()` to load all table definitions. **No ORM models are defined** — only reflected table objects accessed via `metadata.tables["name"]` or `get_table("name")`. All DB calls go through the `get_connection()` context manager which opens a transaction and auto-commits on exit.

### Auth pattern
JWT decoded in `request_handler.py` before routing. `user_id` and `role` injected into `obj` as `_user_id` and `_role`. Services check `obj.get("_user_id")` / `obj.pop("_user_id")` directly — no middleware layer.

**Roles:** `CUSTOMER`, `PROVIDER`, `ADMIN`, `SUPPORT`. Access tokens expire in 15 min; refresh tokens in 30 days (stored in `refresh_tokens` table, revoked on logout). Tokens carry `typ: access|refresh`; decode only via `utilities/auth_tokens.py` (`decode_access_token` / `decode_refresh_token`). `JWT_SECRET` is required — the Lambda fails at cold start without it.

### Redis usage
- OTP storage: `otp:{phone}` (10-min TTL, SHA-256 hashed)
- OTP rate limiting: `otp_rate:{phone}` (counter, 1-hr TTL, max 5)
- OTP brute-force guard: `otp_attempts:{phone}` (OTP deleted after 5 wrong tries)
- Singleton client in `utilities/redis_connection.py`. The in-memory fallback is local-dev only; when `ENVIRONMENT` is staging/production an unreachable Redis raises.

### Secrets
In Lambda: `env_loader.load_secrets()` fetches JSON from AWS Secrets Manager (`SECRET_NAME` env var) and sets all keys into `os.environ`. Locally: skip `SECRET_NAME` and set vars directly in `.env`.

### Push notifications
Uses Expo Push API (`https://exp.host/--/api/v2/push/send`). Tokens must start with `ExponentPushToken[`. Push is always non-fatal (wrapped in try/except). Also writes to `in_app_notifications` table (even for users with no push token). WebSocket pushes go through `utilities/ws_push.py`, which deletes stale connections on `GoneException`.

### Payment flow
Razorpay: `POST /payments/create-order` → client completes payment → `POST /payments/verify` (HMAC signature check). Platform fee applied on verify: `PLATFORM_FEE_PCT` % (default 10%) deducted from provider earnings.

### Booking pricing
All amounts are computed server-side in `bookings/booking_pricing.py` from `services.base_price` / `sub_services.price`; client `sub_total`/`discount`/`total_amount` are ignored. Coupon, subscription quota and coins are applied (and reserved atomically) inside booking creation.

### Booking status machine
```
PENDING → ACCEPTED            POST/PATCH /bookings/{id}/accept (approved providers only, atomic claim)
ACCEPTED → EN_ROUTE           PATCH /status (provider)
ACCEPTED|EN_ROUTE → IN_PROGRESS   POST /otp-verify only (door OTP cannot be skipped)
IN_PROGRESS → COMPLETED       POST /complete (provider) or admin
PENDING|ACCEPTED → CANCELLED  customer (own bookings) / admin; admin also from EN_ROUTE, IN_PROGRESS
```
Transitions are role-gated via `ALLOWED_TRANSITIONS` in `bookings/bookings_service.py` and written with `update_status(..., expected_status=...)` so concurrent requests can't overwrite each other. Door OTP is 4 digits.

## Key env vars
| Var | Purpose |
|-----|---------|
| `DB_URL` | MySQL connection string (`mysql+pymysql://...`) |
| `JWT_SECRET` | HS256 signing key |
| `REDIS_URL` | Redis connection URL |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Payment gateway |
| `MSG91_AUTH_KEY` | SMS OTP provider (if unset, OTP printed to stdout) |
| `S3_DOCUMENTS_BUCKET` | Provider KYC docs bucket |
| `S3_MEDIA_BUCKET` | General media (proof photos, etc.) |
| `WEBSOCKET_ENDPOINT_URL` | API GW Management API URL for WS broadcasting |
| `PLATFORM_FEE_PCT` | Platform cut from payments (default: 10) |
| `ENVIRONMENT` | `staging` / `production` (set by template). Enables fail-closed Redis, disables master OTP in production, hides OTPs from logs |
| `EXOTEL_SID` / `EXOTEL_API_KEY` / `EXOTEL_API_TOKEN` | Exotel account credentials (masked calling) |
| `EXOTEL_SUBDOMAIN` | `api.exotel.com` or `api.in.exotel.com` — must match the Exotel account |
| `EXOPHONE` | Exotel virtual number shown to both parties |
| `EXOTEL_STATUS_CALLBACK_URL` | `https://<api>/Prod/calls/status-callback?token=<EXOTEL_CALLBACK_SECRET>` |
| `EXOTEL_CALLBACK_SECRET` | Required when deployed — callbacks without it are rejected |
| `EXOTEL_RECORD` / `EXOTEL_TIME_LIMIT_SEC` | Optional: `true` to record calls; max call length (default 1800) |

### Masked calling
`calls/exotel_client.py` wraps Exotel Connect (credentials via basic auth — never in the URL). `POST /calls/initiate` rings the caller first, then bridges to the other party; both see only the ExoPhone. Server-side rate limit (20 s cooldown, 6 calls / 15 min per user per booking). Failed attempts are stored as `FAILED` rows with a safe `error_message`. `POST /calls/status-callback` accepts JSON or form-encoded bodies and stores status, duration, start/end (IST→UTC) and recording URL. `GET /calls/{id}` (participants) and `GET /admin/calls` (admin/support). Run `migrations/002_call_logs.sql` for the detail columns.

## Live Staging URL
`https://g61ebs8u40.execute-api.ap-south-1.amazonaws.com/Prod/` (the apps' `EXPO_PUBLIC_API_BASE_URL`)

DB: MySQL 8 on RDS t3.micro | Redis: redis.io external (not ElastiCache)

## Adding a new endpoint
1. Create `<module>/<module>_modal.py`, `<module>_validator.py`, `<module>_service.py`
2. Instantiate the service in `routing.py` at module level
3. Add route tuples to the `ROUTES` list: `("METHOD", r"/path/pattern/(?P<id>[^/]+)", _svc.method, ["id"])`
4. Service method signature: `def method(self, obj, connection)` — returns `("success"|"created", data)`

## Utility helpers
`utilities/common_table_elements.py` provides `new_uuid()`, `now_utc()`, `strip_private_keys(obj)`, `paginate(query, page, per_page)`.
