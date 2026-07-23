from pydantic import BaseModel, Field
from typing import Optional, List


class SetServicesSchema(BaseModel):
    service_ids: List[str] = Field(..., min_length=1)


class SetDocumentsSchema(BaseModel):
    aadhaar_front: Optional[str] = None
    aadhaar_back:  Optional[str] = None
    pan:           Optional[str] = None


class UpdateProviderProfileSchema(BaseModel):
    bio: Optional[str] = None
    years_experience: Optional[int] = None
    bank_account_number: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_account_name: Optional[str] = None


class UpdateLocationSchema(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class ToggleAvailabilitySchema(BaseModel):
    is_available: bool


class NearbyProvidersSchema(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(20.0, gt=0, le=100)
    service_id: Optional[str] = None
