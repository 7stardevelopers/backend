import re
from auth.authorization_service import AuthorizationService
from bookings.bookings_service import BookingsService
from instant_bookings.instant_bookings_service import InstantBookingsService
from services_catalog.services_service import ServicesService
from payments.payment_service import PaymentService
from coupons.coupons_service import CouponsService
from reviews.reviews_service import ReviewsService
from support.support_service import SupportService
from subscriptions.subscriptions_service import SubscriptionsService
from notifications.notifications_service import NotificationsService
from places.places_service import PlacesService
from providers.providers_service import ProvidersService
from documents.documents_service import DocumentsService
from admin.admin_service import AdminService
from finance.finance_service import FinanceService

_auth   = AuthorizationService()
_books  = BookingsService()
_ibooks = InstantBookingsService()
_svcs   = ServicesService()
_pay    = PaymentService()
_coupons = CouponsService()
_rev    = ReviewsService()
_supp   = SupportService()
_subs   = SubscriptionsService()
_notif  = NotificationsService()
_places = PlacesService()
_prov   = ProvidersService()
_docs   = DocumentsService()
_admin  = AdminService()
_fin    = FinanceService()

# (METHOD, path_pattern, handler, param_names)
# param_names: list of path param names extracted via regex group
ROUTES = [
    # AUTH
    ("POST", "/auth/send-otp",              _auth.send_otp,              []),
    ("POST", "/auth/verify-otp",            _auth.verify_otp,            []),
    ("POST", "/auth/refresh",               _auth.refresh_token,         []),
    ("GET",  "/auth/me",                    _auth.get_profile,           []),
    ("PATCH","/auth/me",                    _auth.update_profile,        []),
    ("POST", "/auth/logout",               _auth.logout,                []),

    # SERVICES CATALOG
    ("GET",  "/categories",                 _svcs.list_categories,       []),
    ("GET",  "/services/search",            _svcs.search,                []),
    ("GET",  "/services",                   _svcs.list_services,         []),
    ("GET",  r"/services/(?P<id>[^/]+)",    _svcs.get_detail,            ["id"]),

    # BOOKINGS
    ("POST", "/bookings",                   _books.create,               []),
    ("GET",  "/bookings",                   _books.list_mine,            []),
    ("GET",  r"/bookings/(?P<id>[^/]+)$",   _books.get_detail,           ["id"]),
    ("PATCH",r"/bookings/(?P<id>[^/]+)/status", _books.update_status,    ["id"]),
    ("POST", r"/bookings/(?P<id>[^/]+)/cancel",  _books.cancel,          ["id"]),
    ("POST", r"/bookings/(?P<id>[^/]+)/otp-verify", _books.verify_door_otp, ["id"]),
    ("POST", r"/bookings/(?P<id>[^/]+)/tip",   _books.add_tip,           ["id"]),
    ("POST", r"/bookings/(?P<id>[^/]+)/rebook", _books.rebook,           ["id"]),

    # INSTANT BOOKINGS
    ("POST", "/instant-bookings",           _ibooks.create_instant,      []),
    ("GET",  r"/instant-bookings/(?P<id>[^/]+)", _ibooks.get_status,     ["id"]),

    # PAYMENTS
    ("POST", "/payments/create-order",      _pay.create_order,           []),
    ("POST", "/payments/verify",            _pay.verify_payment,         []),
    ("POST", "/payments/payout-request",    _pay.payout_request,         []),
    ("POST", "/payments/refund",            _pay.request_refund,         []),

    # COUPONS
    ("POST", "/coupons/validate",           _coupons.validate,           []),
    ("GET",  "/coupons",                    _coupons.list_active,        []),
    ("POST", "/coupons",                    _coupons.admin_create,       []),
    ("DELETE",r"/coupons/(?P<id>[^/]+)",    _coupons.admin_delete,       ["id"]),

    # REVIEWS
    ("POST", "/reviews",                    _rev.create,                 []),
    ("GET",  r"/reviews/service/(?P<id>[^/]+)", _rev.list_for_service,   ["id"]),
    ("GET",  r"/reviews/provider/(?P<id>[^/]+)", _rev.list_for_provider, ["id"]),
    ("DELETE",r"/reviews/(?P<id>[^/]+)",    _rev.admin_delete,           ["id"]),

    # PROVIDERS
    ("GET",  "/providers/me",               _prov.get_my_profile,        []),
    ("PATCH","/providers/me",               _prov.update_profile,        []),
    ("PATCH","/providers/me/availability",  _prov.toggle_availability,   []),
    ("PATCH","/providers/me/location",      _prov.update_location,       []),
    ("GET",  "/providers/nearby",           _prov.get_nearby,            []),
    ("GET",  "/providers",                  _prov.admin_list,            []),
    ("GET",  r"/providers/(?P<id>[^/]+)$",  _prov.get_public_profile,    ["id"]),
    ("PATCH",r"/providers/(?P<id>[^/]+)/approve", _prov.admin_approve,   ["id"]),
    ("PATCH",r"/providers/(?P<id>[^/]+)/suspend",  _prov.admin_suspend,  ["id"]),

    # DOCUMENTS
    ("POST", "/documents/upload-url",       _docs.generate_upload_url,   []),
    ("POST", "/documents/confirm",          _docs.confirm_upload,        []),
    ("GET",  "/documents/mine",             _docs.list_mine,             []),
    ("GET",  r"/documents/(?P<providerId>[^/]+)$", _docs.admin_list,     ["providerId"]),
    ("PATCH",r"/documents/(?P<id>[^/]+)/verify", _docs.admin_verify,     ["id"]),

    # SUPPORT
    ("POST", "/support/tickets",            _supp.create_ticket,         []),
    ("GET",  "/support/tickets/all",        _supp.admin_list,            []),
    ("GET",  "/support/tickets",            _supp.list_mine,             []),
    ("GET",  r"/support/tickets/(?P<id>[^/]+)$", _supp.get_detail,       ["id"]),
    ("PATCH",r"/support/tickets/(?P<id>[^/]+)$", _supp.update_status,    ["id"]),
    ("POST", r"/support/tickets/(?P<id>[^/]+)/messages", _supp.reply,    ["id"]),

    # SUBSCRIPTIONS
    ("GET",  "/subscriptions/plans",        _subs.list_plans,            []),
    ("POST", "/subscriptions/subscribe",    _subs.subscribe,             []),
    ("GET",  "/subscriptions/mine",         _subs.get_mine,              []),
    ("POST", "/subscriptions/cancel",       _subs.cancel,                []),

    # NOTIFICATIONS
    ("POST", "/notifications/register",     _notif.register_token,       []),
    ("DELETE","/notifications/token",       _notif.unregister_token,     []),
    ("GET",  "/notifications",              _notif.list_in_app,          []),
    ("PATCH",r"/notifications/(?P<id>[^/]+)/read", _notif.mark_read,     ["id"]),
    ("POST", "/notifications/announce",     _notif.admin_announce,       []),

    # PLACES
    ("GET",  "/places/autocomplete",        _places.autocomplete,        []),
    ("GET",  "/places/details",             _places.details,             []),

    # ADMIN
    ("GET",  "/admin/dashboard",            _admin.dashboard_stats,      []),
    ("GET",  "/admin/bookings",             _admin.list_all_bookings,    []),
    ("PATCH",r"/admin/bookings/(?P<id>[^/]+)", _admin.update_booking,    ["id"]),
    ("GET",  "/admin/users",                _admin.list_users,           []),
    ("POST", "/admin/services",             _admin.create_service,       []),
    ("PATCH",r"/admin/services/(?P<id>[^/]+)", _admin.update_service,    ["id"]),
    ("DELETE",r"/admin/services/(?P<id>[^/]+)", _admin.delete_service,   ["id"]),

    # FINANCE
    ("GET",  "/finance/overview",           _fin.overview,               []),
    ("GET",  "/finance/payouts",            _fin.payout_queue,           []),
    ("PATCH",r"/finance/payouts/(?P<id>[^/]+)", _fin.approve_payout,     ["id"]),
    ("GET",  "/finance/reports",            _fin.export_report,          []),
]


def dispatch_rest(method, path, obj, connection, user_id, role):
    obj["_user_id"] = user_id
    obj["_role"] = role

    for route_method, pattern, handler_fn, param_names in ROUTES:
        if route_method != method:
            continue
        m = re.fullmatch(pattern, path)
        if m:
            for name in param_names:
                obj[name] = m.group(name)
            return handler_fn(obj, connection)

    raise ValueError(f"Route not found: {method} {path}")
