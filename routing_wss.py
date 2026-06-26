from web_sockets.web_sockets_service import WebSocketsService

_wss = WebSocketsService()

WSS_ROUTES = {
    "$connect":      _wss.on_connect,
    "$disconnect":   _wss.on_disconnect,
    "sendMessage":   _wss.on_message,
    "locationUpdate": _wss.on_location,
    "markDelivered": _wss.on_mark_delivered,
    "$default":      _wss.on_default,
}


def dispatch_wss(route, connection_id, event, conn):
    handler_fn = WSS_ROUTES.get(route, _wss.on_default)
    return handler_fn(connection_id, event, conn)
