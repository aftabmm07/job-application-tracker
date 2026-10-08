from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobApplication(Base):
    __tablename__ = "job_applications"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    company_name: Mapped[str] = mapped_column(
        String(150), nullable=False
    )

    job_title: Mapped[str] = mapped_column(
        String(200), nullable=False
    )

    location: Mapped[str | None] = mapped_column(
        String(150), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="saved"
    )

    application_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )

    job_description: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )
