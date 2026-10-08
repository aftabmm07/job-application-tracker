# Job Application Tracker

A web-based job application management dashboard for tracking applications, managing application statuses, analyzing job descriptions, and improving the job search workflow.

## Project Goals

The application will allow users to:

- Add and manage job applications
- Track application status
- Store job descriptions and job links
- Track interviews and follow-ups
- View job application statistics through a dashboard
- Analyze job descriptions
- Compare job requirements with a CV
- Identify relevant skills and keywords

## Planned Tech Stack

- Frontend: React
- Backend: Python / FastAPI
- Database: PostgreSQL
- Containerization: Docker
- CI/CD: GitHub Actions
- Version Control: Git & GitHub

## Project Status

     In development

## Development Roadmap

1. Project setup
2. Backend API
3. Database integration
4. Frontend dashboard
5. Job application management
6. Job description analysis
7. CV matching
8. Testing
9. Dockerization
10. CI/CD pipeline
11. Cloud deployment


## Current Implementation

### Backend
- Python with FastAPI
- REST API endpoints
- Automated testing with Pytest
- GitHub Actions CI pipeline

### Database
- PostgreSQL 17 running in Docker
- Docker Compose for local database setup
- SQLAlchemy for database connectivity
- Psycopg PostgreSQL driver
- Environment-based database credentials
- Persistent PostgreSQL storage using Docker volumes

### Available API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | API welcome message |
| GET | `/health` | API health check |
| GET | `/health/db` | PostgreSQL connectivity check |
| GET | `/docs` | Interactive API documentation |

## Quick Start

1. Clone the repository.
2. Create a `.env` file in the project root containing `POSTGRES_PASSWORD`.
3. Start PostgreSQL using `docker compose up -d`.
4. Install backend dependencies from `backend/requirements.txt`.
5. Start FastAPI using `python -m uvicorn app.main:app --reload` from the `backend` directory.
6. Open http://127.0.0.1:8000/docs to explore the API.

For detailed setup instructions, see [Development Setup Guide](docs/setup.md).

## Development Status

The project is under active development.

Completed:
- FastAPI backend initialization
- Backend unit tests
- GitHub Actions CI
- PostgreSQL integration and connectivity checks

Planned:
- Job application database models
- CRUD API endpoints
- React frontend
- Job application status tracking
- Job description analysis and CV matching
- Full application containerization and deployment
