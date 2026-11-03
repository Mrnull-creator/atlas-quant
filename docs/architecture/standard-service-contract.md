# Atlas Quant



## Standard Service Contract v1.0



**Status:** Accepted


**Version:** 1.0



**Date:** 3 November 2026



### 1. Purpose



This document establishes the common architectural, technical, operational, and development standards that all Atlas Quant services must follow.



The objective is to ensure consistency, maintainability, independent service ownership, and predictable integration across the platform.



### 2. Technology standards



Services will follow the approved Atlas Quant technology baseline:



- Python 3.14

- FastAPI

- Pydantic 2

- SQLAlchemy 2 and Alembic where database persistence is required

- PostgreSQL 18

- Redpanda for asynchronous event communication where required

- Pytest
- Structured logging and OpenTelemetry

- Docker Compose for initial deployment



Dependency versions must be verified and pinned.



### 3. Service identity



Every service must define:



- Unique service name.

- Service version.

- Runtime environment.

- Configuration settings.

- Application entry point.



Service identity must be available through the shared Platform Foundation.



### 4. Configuration management



Services must use validated configuration.



- Environment-specific configuration must be supported.

- Environment variables must follow the `ATLAS_` prefix convention.

- Secrets must never be embedded in source code.

- Sensitive configuration values must not appear in logs.

- Research mode must remain the default operating mode.



### 5. Application lifecycle



Every service must provide a consistent application lifecycle.



- Application initialization.

- Dependency initialization where required.

- Startup validation.

- Application operation.

- Graceful shutdown.

- Resource cleanup.



FastAPI application lifecycle mechanisms should be used for startup and shutdown management.



### 6. Health and readiness



Every HTTP service must expose:



- `/health/live` — indicates whether the application process is alive.

- `/health/ready` — indicates whether the service is ready to accept requests.



Liveness and readiness must be treated as separate concepts.



Readiness checks may include required infrastructure dependencies.



### 7. Logging and error handling



Services must use the shared Platform Foundation.



- Consistent logging format.

- Appropriate log levels.

- Service identification in log records.

- Standard application exceptions.

- Consistent API error responses.

- No exposure of secrets or sensitive information.



### 8. API standards



Services exposing HTTP APIs must follow consistent conventions.



- REST-oriented API design where appropriate.

- Versioned API paths, beginning with `/api/v1`.

- Pydantic request and response models.

- Explicit HTTP status codes.

- Input validation.

- Consistent error responses.

- Automatically generated API documentation.



Internal service implementation details must not become public API contracts.



### 9. Data ownership



Each service is the authoritative owner of its business data.



- Services must not directly modify another service's database.

- Access to another service's data must occur through its APIs or published events.

- Database schemas must maintain independent service ownership.

- Database migrations must be controlled by the owning service.

- Services without persistent business data do not require a database.



### 10. Event communication



Services using asynchronous communication must follow common event conventions.



Event contracts must include:



- Unique event identification.

- Event type.

- Event version.

- Producer identification.

- Event timestamp.

- Correlation identification where applicable.

- Structured event payload.



Consumers must safely handle duplicate delivery.



Event schemas must be documented and versioned.



### 11. Testing standards



Every service must include appropriate tests.



- Unit tests.

- API tests where applicable.

- Integration tests where infrastructure dependencies exist.

- Configuration validation tests.

- Health and readiness tests.

- Error-handling tests.



Tests must be repeatable and must not require live trading or real broker orders.



### 12. Security requirements



- Least-privilege access.

- Secure configuration.

- Input validation.

- Controlled service-to-service communication.

- Protection of sensitive information.

- No secrets in source control.



### 13. Trading safety



Research remains the default operating mode.



The Execution Service initially supports research and simulated execution only.



Live broker connectivity and real-money order submission require a separate architecture decision, explicit authorization, safeguards, and validation.



### 14. Deployment standards



Docker Compose will be used for initial service deployment.



Services must remain logically independent even when sharing infrastructure.



Independent deployment and scaling will be introduced when justified.



Kubernetes remains deferred.



### 15. Adoption and enforcement



New Atlas Quant services must follow this contract.



Existing modules will be migrated incrementally without unnecessary disruption to working research capabilities.



Changes to this standard require documented review and approval.



### 16. Review



This standard should be reviewed as Atlas Quant evolves and operational requirements become clearer.



