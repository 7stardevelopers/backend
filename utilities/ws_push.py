import json
import os
import boto3

from utilities.db_connection import get_table

_client = None


def _get_client():
    global _client
    endpoint = os.environ.get("WEBSOCKET_ENDPOINT_URL", "")
    if not endpoint:
        return None
    if _client is None:
        _client = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=endpoint,
            region_name=os.environ.get("AWS_REGION_NAME", "ap-south-1"),
        )
    return _client


def push_to_user(conn, user_id: str, payload: dict):
    """Send payload to every open WebSocket connection of user_id. Non-fatal."""
    client = _get_client()
    if client is None:
        return
    ws_t = get_table("ws_connections")
    rows = conn.execute(ws_t.select().where(ws_t.c.user_id == str(user_id))).fetchall()
    push_to_connections(conn, [r.connection_id for r in rows], payload)


def push_to_connections(conn, connection_ids: list, payload: dict):
    client = _get_client()
    if client is None or not connection_ids:
        return
    data = json.dumps(payload, default=str).encode()
    stale = []
    for cid in connection_ids:
        try:
            client.post_to_connection(ConnectionId=cid, Data=data)
        except client.exceptions.GoneException:
            stale.append(cid)
        except Exception as e:
            print(f"[WS] post_to_connection failed for {cid} (non-fatal): {e}")
    if stale:
        ws_t = get_table("ws_connections")
        conn.execute(ws_t.delete().where(ws_t.c.connection_id.in_(stale)))
