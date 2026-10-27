"""Auth-only request/response schemas. HTTP validation errors are sanitized."""
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from email_validator import EmailNotValidError, validate_email
from ipaddress import ip_address
from pydantic import HttpUrl, TypeAdapter

from app.passwords import validate_password


def normalize_auth_email(value):
    """Normalize account email without DNS lookups or echoing invalid input."""
    if not isinstance(value, str):
        raise ValueError("Invalid email")
    try:
        # Do not perform DNS/network checks or accept display-name syntax.
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise ValueError("Invalid email") from None


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    email: str = Field(max_length=320)
    password: SecretStr

    @field_validator("email", mode="before")
    @classmethod
    def normalized_email(cls, value):
        return normalize_auth_email(value)

    @field_validator("password", mode="before")
    @classmethod
    def password_policy(cls, value):
        validate_password(value)
        return value


class IdentityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str


_https_url = TypeAdapter(HttpUrl)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    full_name: str | None = Field(default=None, max_length=120)
    professional_headline: str | None = Field(default=None, max_length=160)
    location: str | None = Field(default=None, max_length=120)
    linkedin_url: str | None = Field(default=None, max_length=500)
    github_url: str | None = Field(default=None, max_length=500)
    portfolio_url: str | None = Field(default=None, max_length=500)

    @field_validator("full_name", "professional_headline", "location", mode="before")
    @classmethod
    def optional_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Invalid profile text")
        return value.strip() or None

    @field_validator("linkedin_url", "github_url", "portfolio_url", mode="before")
    @classmethod
    def professional_url(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Invalid profile URL")
        value = value.strip()
        if not value:
            return None
        if len(value) > 500 or any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("Invalid profile URL")
        try:
            parsed = _https_url.validate_python(value)
            host = parsed.host or ""
            if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                    or "." not in host or host.endswith(".local") or host.endswith(".internal")):
                raise ValueError
            try:
                ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError
            return str(parsed)
        except ValueError:
            raise ValueError("Invalid profile URL") from None


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    email: str
    full_name: str | None
    professional_headline: str | None
    location: str | None
    linkedin_url: str | None
    github_url: str | None
    portfolio_url: str | None


class RegistrationResponse(BaseModel):
    message: str = "Registration received. Account activation is required before sign-in."


class ResendVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    email: str = Field(max_length=320)

    @field_validator("email", mode="before")
    @classmethod
    def normalized_email(cls, value):
        return normalize_auth_email(value)


class ResendVerificationResponse(BaseModel):
    message: str = "If this address needs verification, instructions will be sent when available."


class EmailVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    token: SecretStr

    @field_validator("token", mode="before")
    @classmethod
    def bounded_token(cls, value):
        if not isinstance(value, str) or len(value) > 256:
            raise ValueError("Invalid verification request")
        # Format and eligibility failures use the same generic service result.
        return value


class EmailVerificationResponse(BaseModel):
    message: str = "Email verified. You can now sign in."


class CsrfResponse(BaseModel):
    csrf_token: str
