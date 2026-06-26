import json
from utilities.env_loader import load_secrets
from utilities.db_connection import get_connection
from request_handler import parse_request
from routing import dispatch_rest
from routing_wss import dispatch_wss

load_secrets()


def handler(event, context):
    if event.get("requestContext", {}).get("connectionId"):
        return handle_websocket(event, context)
    return handle_rest(event, context)


def handle_rest(event, context):
    try:
        req = parse_request(event)
        with get_connection() as conn:
            status, data = dispatch_rest(
                method=req["method"],
                path=req["path"],
                obj=req["body"],
                connection=conn,
                user_id=req["user_id"],
                role=req["role"],
            )
        code = {"success": 200, "created": 201}.get(status, 400)
        return response(code, {"status": status, "data": data})
    except PermissionError as e:
        return response(403, {"status": "error", "message": str(e)})
    except ValueError as e:
        return response(400, {"status": "error", "message": str(e)})
    except Exception as e:
        print(f"[ERROR] Unhandled: {e}")
        return response(500, {"status": "error", "message": "Internal server error"})


def handle_websocket(event, context):
    route = event["requestContext"]["routeKey"]
    conn_id = event["requestContext"]["connectionId"]
    try:
        with get_connection() as conn:
            status, data = dispatch_wss(route, conn_id, event, conn)
    except Exception as e:
        print(f"[WSS ERROR] {e}")
    return {"statusCode": 200, "body": "OK"}


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
