# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Local development (activate venv first):**
```bash
# Windows
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run locally via SAM
sam local start-api --env-vars env.json

# Deploy to staging
sam build && sam deploy --guided

# Deploy to production
sam build && sam deploy --config-env prod
```

**Environment setup for local testing:**
Copy `.env` to `env.json` in SAM-compatible format, or set env vars directly. When `SECRET_NAME` is not set, `env_loader.py` skips Secrets Manager and uses whatever is already in `os.environ` — this is the local dev path.

## Architecture

### Single-Lambda design
Everything runs in one AWS Lambda function (`lambda_function.handler`). The handler distinguishes REST from WebSocket by checking `event.requestContext.connectionId`. A second Lambda (`location_trigger.handler`) runs on a 15-minute EventBridge schedule to send pre-booking push notifications.

### Request flow (REST)
```
API Gateway → lambda_function.handle_rest
  → request_handler.parse_request      # extracts method, path, body, JWT user_id/role
  → routing.dispatch_rest              # regex pattern match → service method
  → <module>/service.py method(obj, connection)
```

`obj` always contains the merged body + query params + `_user_id` + `_role`. Path params (e.g. `id` from `/bookings/{id}`) are injected into `obj` by the dispatcher. Services strip `_`-prefixed keys before passing to validators.

### Request flow (WebSocket)
```
API Gateway WebSocket → lambda_function.handle_websocket
  → routing_wss.dispatch_wss → web_sockets/web_sockets_service.py
```
Routes: `$connect`, `$disconnect`, `sendMessage`, `locationUpdate`, `markDelivered`.

### Module structure
Every feature module follows the same three-file pattern:
- `*_modal.py` — SQLAlchemy CRUD (uses `metadata.tables["table_name"]`)
- `*_validator.py` — Pydantic v2 schemas for input validation
- `*_service.py` — business logic; instantiated once at module level in `routing.py`

### Database access
`db_connection.py` creates a single SQLAlchemy engine at cold-start and calls `metadata.reflect()` to load all table definitions. Modals access tables via `metadata.tables["name"]` — **no ORM models are defined**, only reflected table objects. All DB calls go through the `get_connection()` context manager which opens a transaction and auto-commits on exit.

### Auth pattern
JWT is decoded in `request_handler.py` before routing. `user_id` and `role` are injected into `obj` as `_user_id` and `_role`. Services check `obj.get("_user_id")` directly — there is no middleware layer. Access tokens expire in 15 min; refresh tokens in 30 days (stored in `refresh_tokens` table).

### Redis usage
- OTP storage: `otp:{phone}` (10-min TTL, SHA-256 hashed)
- OTP rate limiting: `otp_rate:{phone}` (counter, 1-hr TTL, max 5)
- Singleton client in `utilities/redis_connection.py`

### Secrets
In Lambda: `env_loader.load_secrets()` runs at cold-start, fetches JSON from AWS Secrets Manager (`SECRET_NAME` env var), and sets all keys into `os.environ`. Locally: skip `SECRET_NAME` and set vars directly in `.env`.

## Live Staging URL

`https://1ipuylc4mh.execute-api.ap-south-1.amazonaws.com/Prod/`

DB: MySQL 8 on RDS t3.micro | Redis: redis.io external (not ElastiCache)

### Adding a new endpoint
1. Create `<module>/<module>_modal.py`, `<module>_validator.py`, `<module>_service.py`
2. Instantiate the service in `routing.py` at module level
3. Add route tuples to the `ROUTES` list: `("METHOD", r"/path/pattern/(?P<id>[^/]+)", _svc.method, ["id"])`

### Recent additions (as of 2026-06-29)
- `POST /admin/services/{id}/sub-categories` — creates a sub_category + all its sub_service items in one call
  - Handler: `_admin.create_sub_category` in `admin/admin_service.py`
  - Seeding script: `backend/seed_services.py` (uses this endpoint; DB was seeded directly via SQL instead)
