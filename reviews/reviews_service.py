from reviews.reviews_modal import ReviewsMaster
from reviews.reviews_validator import CreateReviewSchema
from providers.providers_modal import ProvidersMaster
from bookings.bookings_modal import BookingsMaster


class ReviewsService:
    def __init__(self):
        self.modal = ReviewsMaster()
        self.provider_modal = ProvidersMaster()
        self.booking_modal = BookingsMaster()

    def create(self, obj, connection):
        user_id = obj.pop("_user_id")
        role = obj.pop("_role", None)
        if role != "CUSTOMER":
            raise PermissionError("Only customers can submit reviews")

        data = CreateReviewSchema(**obj)
        booking = self.booking_modal.read_one(connection, data.booking_id)
        if str(booking["customer_id"]) != str(user_id):
            raise PermissionError("Access denied")
        if booking["status"] != "COMPLETED":
            raise ValueError("Can only review completed bookings")
        if self.modal.find_by_booking(connection, data.booking_id):
            raise ValueError("Review already submitted for this booking")

        review_data = {
            "booking_id": data.booking_id,
            "customer_id": user_id,
            "provider_id": booking["provider_id"],
            "service_id": booking["service_id"],
            "rating": data.rating,
            "comment": data.comment,
        }
        review = self.modal.create(connection, review_data)

        avg, count = self.modal.get_avg_rating(connection, booking["provider_id"])
        self.provider_modal.update_rating(connection, booking["provider_id"], avg, count)

        return "created", review

    def list_for_service(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        service_id = obj.get("id") or obj.get("service_id")
        page = int(obj.get("page", 1))
        reviews = self.modal.list_for_service(connection, service_id, page)
        return "success", reviews

    def list_for_provider(self, obj, connection):
        obj.pop("_user_id", None)
        obj.pop("_role", None)
        provider_id = obj.get("id") or obj.get("provider_id")
        page = int(obj.get("page", 1))
        reviews = self.modal.list_for_provider(connection, provider_id, page)
        return "success", reviews

    def admin_delete(self, obj, connection):
        role = obj.pop("_role", None)
        obj.pop("_user_id", None)
        if role != "ADMIN":
            raise PermissionError("Admin role required")
        review_id = obj.get("id") or obj.get("review_id")
        self.modal.soft_delete(connection, review_id)
        return "success", {"message": "Review deleted"}
