"""Auth-only request/response schemas. HTTP validation errors are sanitized."""
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from email_validator import EmailNotValidError, validate_email

from app.passwords import validate_password


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    email: str = Field(max_length=320)
    password: SecretStr

    @field_validator("email", mode="before")
    @classmethod
    def normalized_email(cls, value):
        if not isinstance(value, str):
            raise ValueError("Invalid email")
        try:
            # Do not perform DNS/network checks or accept display-name syntax.
            return validate_email(value.strip(), check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            raise ValueError("Invalid email") from None

    @field_validator("password", mode="before")
    @classmethod
    def password_policy(cls, value):
        validate_password(value)
        return value


class IdentityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str


class RegistrationResponse(BaseModel):
    message: str = "Registration received. Account activation is required before sign-in."


class CsrfResponse(BaseModel):
    csrf_token: str
