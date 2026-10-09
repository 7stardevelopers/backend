import json
import time
import traceback
from utilities.env_loader import load_secrets
from utilities.db_connection import get_connection, get_engine
from utilities.auth_tokens import get_jwt_secret
from utilities.api_logger import log_api

# Must run before routing imports: routing.py instantiates all services at module
# level, whose __init__ methods access metadata.tables[...] which requires reflect().
load_secrets()
get_jwt_secret()  # fail at cold start rather than serve requests with a missing key
get_engine()

from request_handler import parse_request
from routing import dispatch_rest, RouteNotFound
from routing_wss import dispatch_wss
from media.media_service import sign_media_urls, strip_media_signatures


def handler(event, context):
    if event.get("requestContext", {}).get("connectionId"):
        return handle_websocket(event, context)
    return handle_rest(event, context)


def handle_rest(event, context):
    method = event.get("httpMethod", "?")
    path = event.get("path", "?")
    if method == "OPTIONS":
        return response(200, {"status": "success", "data": None})
    started = time.perf_counter()
    req = {}
    req_body = None
    error = trace = None
    html = None
    try:
        req = parse_request(event)
        method, path = req["method"], req["path"]
        req["body"] = strip_media_signatures(req["body"])
        # Snapshot before dispatch — services pop keys out of obj.
        req_body = dict(req["body"]) if isinstance(req["body"], dict) else req["body"]
        if "raw_body" in req:  # after the snapshot so the raw payload isn't logged twice
            req["body"]["_raw_body"] = req["raw_body"]
            req["body"]["_rzp_signature"] = req["rzp_signature"]
        with get_connection() as conn:
            status, data = dispatch_rest(
                method=req["method"],
                path=req["path"],
                obj=req["body"],
                connection=conn,
                user_id=req["user_id"],
                role=req["role"],
            )
        if status == "html":
            html, data, status = data, None, "success"
        code = {"success": 200, "created": 201}.get(status, 400)
        body = {"status": status, "data": data}
        if code >= 400:
            error = str(data)
    except RouteNotFound as e:
        error = str(e)
        code = 404
        body = {"status": "error", "message": error}
    except PermissionError as e:
        error = str(e)
        # Token errors are authentication failures (401), not authorisation (403)
        code = 401 if error in ("Token expired", "Invalid token") else 403
        body = {"status": "error", "message": error}
    except ValueError as e:
        error = str(e)
        code = 400
        body = {"status": "error", "message": error}
    except Exception as e:
        error = f"Unhandled: {e}"
        trace = traceback.format_exc()
        code = 500
        body = {"status": "error", "message": "Internal server error"}

    log_api(
        method, path, code,
        duration_ms=round((time.perf_counter() - started) * 1000),
        user_id=req.get("user_id"),
        role=req.get("role"),
        request_id=getattr(context, "aws_request_id", None),
        req_body=req_body,
        resp_body=body,
        error=error,
        trace=trace,
    )
    # Logged above with plain URLs; the apps get loadable signed ones. Presign's
    # object_url stays plain — the app posts it back to be stored.
    if code < 400 and body.get("data") is not None and path != "/media/presign":
        try:
            body["data"] = sign_media_urls(body["data"])
        except Exception as e:
            print(f"[MEDIA SIGN ERROR] {method} {path}: {e}")
    if html is not None:
        return html_response(code, html)
    return response(code, body)


def handle_websocket(event, context):
    route = event["requestContext"]["routeKey"]
    conn_id = event["requestContext"]["connectionId"]
    try:
        with get_connection() as conn:
            status, data = dispatch_wss(route, conn_id, event, conn)
    except Exception as e:
        print(f"[WSS ERROR] route={route} connection={conn_id}: {e}")
        traceback.print_exc()
        status, data = "error", "Internal error"
    # For $connect the status code decides whether API Gateway accepts the socket
    if route == "$connect" and status != "success":
        return {"statusCode": 401 if data == "Unauthorized" else 500, "body": str(data)}
    return {"statusCode": 200, "body": "OK"}


def html_response(status_code, html):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex",
            "Referrer-Policy": "no-referrer",
        },
        "body": html,
    }


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }
