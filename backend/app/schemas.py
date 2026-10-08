
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApplicationStatus(str, Enum):
    SAVED = "saved"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


class JobApplicationFields(BaseModel):
    @field_validator("company_name", "job_title", check_fields=False)
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Field cannot be empty or contain only spaces")

        return value


class JobApplicationCreate(JobApplicationFields):
    company_name: str = Field(min_length=1, max_length=150)
    job_title: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=150)
    status: ApplicationStatus = ApplicationStatus.SAVED
    application_date: date | None = None
    job_description: str | None = None



class JobApplicationUpdate(JobApplicationFields):
    company_name: str = Field(
        default=None, min_length=1, max_length=150
    )
    job_title: str = Field(
        default=None, min_length=1, max_length=200
    )
    location: str | None = Field(default=None, max_length=150)
    status: ApplicationStatus = None
    application_date: date | None = None
    job_description: str | None = None

    @model_validator(mode="after")
    def validate_required_fields(self):
        required_fields = {"company_name", "job_title", "status"}

        for field in required_fields:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")

        return self

class JobApplicationResponse(JobApplicationCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
