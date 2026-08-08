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

**Run integration tests (requires live DB + Redis — reads .env):**
```bash
python full_test.py
```

**Deploy:**
```bash
sam build && sam deploy --guided          # staging (first time)
sam build && sam deploy --config-env prod # production
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

`obj` is the merged body + query params dict, with `_user_id` and `_role` injected. Path params (e.g. `id` from `/bookings/{id}`) are also injected into `obj` by the dispatcher. Services `pop()` `_user_id`/`_role` from `obj` before passing to Pydantic validators.

**Response convention:** service methods return `("success", data)` → HTTP 200, or `("created", data)` → HTTP 201. `PermissionError` → 403, `ValueError` → 400, uncaught exceptions → 500.

### Request flow (WebSocket)
```
API Gateway WebSocket → lambda_function.handle_websocket
  → routing_wss.dispatch_wss → web_sockets/web_sockets_service.py
```
Routes: `$connect`, `$disconnect`, `sendMessage`, `locationUpdate`, `markDelivered`. JWT is passed as a `?token=` query param on `$connect` (no Authorization header on WebSocket).

### Module structure
Every feature module follows the same three-file pattern:
- `*_modal.py` — SQLAlchemy CRUD (accesses `metadata.tables["table_name"]` via `get_table()`)
- `*_validator.py` — Pydantic v2 schemas for input validation
- `*_service.py` — business logic; instantiated once at module level in `routing.py`

### Database access
`db_connection.py` creates a single SQLAlchemy engine at cold-start and calls `metadata.reflect()` to load all table definitions. **No ORM models are defined** — only reflected table objects accessed via `metadata.tables["name"]` or `get_table("name")`. All DB calls go through the `get_connection()` context manager which opens a transaction and auto-commits on exit.

### Auth pattern
JWT decoded in `request_handler.py` before routing. `user_id` and `role` injected into `obj` as `_user_id` and `_role`. Services check `obj.get("_user_id")` / `obj.pop("_user_id")` directly — no middleware layer.

**Roles:** `CUSTOMER`, `PROVIDER`, `ADMIN`, `SUPPORT`. Access tokens expire in 15 min; refresh tokens in 30 days (stored in `refresh_tokens` table, revoked on logout).

### Redis usage
- OTP storage: `otp:{phone}` (10-min TTL, SHA-256 hashed)
- OTP rate limiting: `otp_rate:{phone}` (counter, 1-hr TTL, max 5)
- Singleton client in `utilities/redis_connection.py`

### Secrets
In Lambda: `env_loader.load_secrets()` fetches JSON from AWS Secrets Manager (`SECRET_NAME` env var) and sets all keys into `os.environ`. Locally: skip `SECRET_NAME` and set vars directly in `.env`.

### Push notifications
Uses Expo Push API (`https://exp.host/--/api/v2/push/send`). Tokens must start with `ExponentPushToken[`. Push is always non-fatal (wrapped in try/except). Also writes to `in_app_notifications` table.

### Payment flow
Razorpay: `POST /payments/create-order` → client completes payment → `POST /payments/verify` (HMAC signature check). Platform fee applied on verify: `PLATFORM_FEE_PCT` % (default 10%) deducted from provider earnings.

### Booking status machine
```
PENDING → ACCEPTED → EN_ROUTE → IN_PROGRESS → COMPLETED
PENDING → CANCELLED
ACCEPTED → CANCELLED
```
Transitions are role-gated via `ALLOWED_TRANSITIONS` in `bookings/bookings_service.py`. Door OTP (4-digit) is generated on booking creation; provider verifies it to move `ACCEPTED → IN_PROGRESS`.

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

## Live Staging URL
`https://1ipuylc4mh.execute-api.ap-south-1.amazonaws.com/Prod/`

DB: MySQL 8 on RDS t3.micro | Redis: redis.io external (not ElastiCache)

## Adding a new endpoint
1. Create `<module>/<module>_modal.py`, `<module>_validator.py`, `<module>_service.py`
2. Instantiate the service in `routing.py` at module level
3. Add route tuples to the `ROUTES` list: `("METHOD", r"/path/pattern/(?P<id>[^/]+)", _svc.method, ["id"])`
4. Service method signature: `def method(self, obj, connection)` — returns `("success"|"created", data)`

## Utility helpers
`utilities/common_table_elements.py` provides `new_uuid()`, `now_utc()`, `strip_private_keys(obj)`, `paginate(query, page, per_page)`.
