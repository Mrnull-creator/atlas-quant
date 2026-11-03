# Atlas Quant



## ADR-002: Service Architecture and Deployment Strategy



**Status:** Accepted



**Version:** 1.0



**Date:** 3 November 2026



### 1. Context



Atlas Quant is intended to become a comprehensive, modular algorithmic trading platform supporting market data acquisition, quantitative research, backtesting, signal generation, portfolio management, risk management, and order execution.



The architecture must support future independent service deployment without introducing unnecessary infrastructure complexity during initial development.



### 2. Decision



Atlas Quant will adopt a modular, service-oriented architecture designed to evolve toward independently deployable microservices.



Service boundaries, data ownership, APIs, and event contracts will be established from the beginning.



Initially, related services may execute within a shared application environment where appropriate. Independent deployment will be introduced incrementally as operational and scaling requirements justify it.



### 3. Logical services



The initial service boundaries are:



1\. Market Data Service

2\. Research Service

3\. Backtesting Service

4\. Signal Service

5\. Risk Management Service

6\. Portfolio Management Service

7\. Execution Service

8\. Monitoring Service



These boundaries represent logical responsibilities, not a requirement to deploy eight separate applications immediately.



### 4. Data ownership



Each service owns its business data and is the authoritative owner of that data.



\- Services must not directly modify another service's database.

\- Other services must access business data through the owning service's documented APIs or published events, rather than querying or modifying its internal tables directly.

\- A shared PostgreSQL server is acceptable initially.

\- Each service must maintain independent logical database or schema ownership.

\- Historical market data will use Parquet where appropriate.



### 5. Communication



Two communication mechanisms will be supported:



**Synchronous communication**



\- HTTP APIs.

\- FastAPI.

\- Versioned API contracts.

\- Explicit request and response models.



**Asynchronous communication**



\- Redpanda event messaging.

\- Documented event schemas.

\- Versioned event contracts.

\- Idempotent event processing.



Event consumers must handle duplicate delivery safely. Event contracts must include sufficient identification and version information to support reliable processing and future evolution.



Not every interaction requires messaging. HTTP APIs should be used when synchronous request-response behavior is appropriate.



### 6. Service conventions



Every service must follow common conventions for:



\- Configuration.

\- Logging.

\- Error handling.

\- Health and readiness.

\- Service metadata.

\- Testing.

\- API documentation.

\- Lifecycle management.



The shared Atlas Quant Platform Foundation provides the initial implementation of these capabilities.



### 7. Deployment strategy



Initial infrastructure will use Docker Compose.



Services may initially share infrastructure while maintaining independent business boundaries.



Independent deployment, scaling, and infrastructure separation will be introduced when justified.



Kubernetes is deferred.



### 8. Security and trading safety



\- Secrets must not be committed to source control.

\- Services must validate incoming requests and events.

\- Service permissions must follow least-privilege principles.

\- Research mode remains the default.

\- Live trading remains disabled.

\- The Execution Service will initially support research and simulated execution only.

\- Connectivity to live brokers and real-money order submission requires a separate architecture decision, explicit authorization, safeguards, validation, and planning.



### 9. Implementation sequence



1\. Establish the standard service contract.

2\. Establish the service directory structure and lifecycle.

3\. Implement the Market Data Service.

4\. Integrate existing market data validation and storage capabilities.

5\. Introduce event messaging when required by service integration.

6\. Develop Research and Backtesting services.

7\. Expand into Signals, Risk, Portfolio, and Execution.



### 10. Consequences



**Benefits**



\- Clear ownership and maintainability.

\- Reduced initial operational complexity.

\- Independent service evolution.

\- Consistent interfaces and data contracts.

\- A scalable path toward distributed deployment.



**Trade-offs**



\- Requires disciplined interface design.

\- Requires explicit data ownership enforcement.

\- Independent deployment will require additional infrastructure later.



### 11. Review



This decision should be revisited when actual service deployment, performance, reliability, or scaling requirements justify a change.


