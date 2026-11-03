# ADR-004: Market Data Persistence Architecture

- **Status:** Accepted

- **Version:** 1.0

- **Decision date:** 2026-11-03

- **Scope:** Atlas Quant — Market Data Service

## 1. Context

The Market Data Service is responsible for authoritative ownership of instrument and provider metadata, historical market data catalogs, ingestion records, validation results, and data quality information.

The service must support multiple asset classes, market data providers, instruments, and timeframes. It must preserve original provider data, provide normalized datasets for downstream research, and maintain sufficient lineage and quality information to make historical datasets reproducible and auditable.

The persistence architecture must also support incremental implementation without coupling the domain and application layers to specific database or file-storage technologies.

This decision follows the service ownership principles established in ADR-002.

## 2. Decision

The Market Data Service will use a hybrid persistence architecture:

- **PostgreSQL** will store structured metadata, catalogs, ingestion records, validation results, and quality information.

- **Parquet** will store historical time-series observations, including normalized market data and preserved original provider datasets.

The persistence implementation will be separated from domain and application logic through repository and storage interfaces.

## 3. PostgreSQL responsibilities

PostgreSQL will own the following structured information:

1. Instruments and their canonical metadata.

2. Market data providers and their capabilities.

3. Provider-specific instrument identifiers and symbol mappings.

4. Trading calendars, weekly sessions, and calendar exceptions.

5. Provider availability schedules and their exceptions.

6. Dataset catalog entries and dataset metadata.

7. Ingestion jobs and ingestion run history.

8. Data validation reports.

9. Data quality findings.

The relational model will preserve important queryable fields as structured columns. Flexible supplementary metadata may use JSONB where appropriate, but JSONB will not replace explicit fields needed for integrity, filtering, or reporting.

Each service remains the authoritative owner of its data. Other services must access Market Data Service information through its APIs and events rather than directly accessing its persistence layer.

## 4. Parquet responsibilities

Parquet will store historical observations:

- Normalized OHLCV bars.

- Normalized bid/ask observations.

- Original provider datasets preserved separately from normalized datasets.

Raw and normalized data will use separate storage zones.

### 4.1 Raw data zone

The raw zone preserves provider-originated data in its original or faithfully retained representation.

Raw data must not be silently overwritten or modified during normalization, validation, or subsequent ingestion.

### 4.2 Normalized data zone

The normalized zone contains provider-independent datasets suitable for research and downstream processing.

Normalization must preserve source lineage so that a normalized observation or dataset can be traced to its provider and ingestion history.

### 4.3 Partitioning

Historical data should be partitioned to support efficient retrieval and manageable storage.

The initial partitioning design will consider:

- Provider.

- Asset class.

- Instrument.

- Timeframe.

- Calendar year and month.

Quote datasets will use timestamp-oriented partitioning appropriate to their observation volume.

The exact physical partitioning scheme will be finalized during Parquet storage implementation and validated against representative workloads.

## 5. Dataset catalog and ingestion history

A dataset is a separately identifiable, versioned collection of historical observations.

An ingestion run records an attempt to acquire and process data from a provider.

These are distinct concepts:

- A dataset describes a stored collection of observations.

- An ingestion run describes the process that produced, extended, or attempted to update a dataset.

One ingestion run may produce multiple datasets. A dataset may be extended or superseded by later ingestion runs.

Catalog metadata must be sufficient to identify and locate a dataset and understand its provenance. It should include, as applicable:

- Dataset identity and version.

- Data kind and format.

- Schema version.

- Provider identity.

- Canonical instrument and provider-specific symbol.

- Asset class and timeframe.

- Coverage boundaries.

- Storage location.

- Record count.

- Checksum or equivalent integrity information.

- Associated ingestion provenance.

- Dataset publication state.

Dataset versioning and ingestion idempotency must prevent duplicate processing from silently creating inconsistent catalog entries.

## 6. Publication and consistency

PostgreSQL and Parquet storage do not share one atomic transaction.

The service must therefore use a controlled publication workflow that avoids advertising incomplete datasets as available.

The initial workflow is:

1. Create an ingestion record and identify the intended dataset.

2. Write data to a temporary or staging location.

3. Validate the written data and its metadata.

4. Publish completed data files to their final storage location.

5. Register or update the dataset catalog in PostgreSQL as available.

6. Record the successful ingestion outcome.

Failure handling must preserve enough state to identify incomplete operations and support recovery.

The implementation must consider:

- Interrupted writes.

- Failed validation.

- Orphaned temporary files.

- Published files without corresponding catalog entries.

- Catalog entries whose files are missing or corrupt.

- Safe retry and idempotent ingestion behavior.

Incomplete datasets must not be presented to consumers as available.

The precise recovery and reconciliation mechanism will be defined during implementation.

## 7. Data integrity, lineage, and immutability

The persistence architecture must support:

- Traceability from normalized datasets to their provider and ingestion source.

- Preservation of original provider data.

- Dataset and schema versioning.

- Dataset integrity checks.

- Reproducible historical research.

- Explicit validation and quality records.

- Safe handling of repeated ingestion attempts.

Validation must identify defects without silently modifying original provider data.

Missing observations must not be synthesized merely to make a dataset appear complete.

## 8. Persistence boundaries and code organization

The domain and application layers must not depend directly on PostgreSQL, SQLAlchemy, filesystem paths, or Parquet implementation details.

Persistence interfaces will be defined in the application layer.

The existing repository protocols in `application/repositories.py` provide the starting point.

The implementation may progressively separate interfaces into focused modules, including:

- Instrument repositories.

- Provider repositories.

- Dataset repositories.

- Ingestion repositories.

- Quality repositories.

- Historical data storage interfaces.

Infrastructure implementations will be organized separately, with an initial structure along the following lines:

- `persistence/postgres/`

- `persistence/parquet/`

The exact module structure will be established incrementally as contracts and implementations are introduced.

## 9. Technology decisions

- PostgreSQL is the relational persistence technology.

- Parquet is the historical observation storage format.

- PyArrow is the intended Parquet implementation.

- SQLAlchemy 2 is the intended relational ORM and database toolkit.

- Alembic is the intended relational schema migration tool.

Dependency versions, driver compatibility, and Python 3.14 support must be verified before installation or adoption.

No dependency installation or upgrade is authorized merely by accepting this architecture decision.

## 10. Implementation plan

Implementation will proceed through the following milestones.

### Milestone 1 — Persistence contracts

- Review existing repository protocols.

- Define dataset catalog contracts.

- Define ingestion persistence contracts.

- Define quality persistence contracts.

- Define historical data storage contracts.

- Clarify identity, lineage, versioning, and idempotency requirements.

- Keep application and domain layers independent of infrastructure.

### Milestone 2 — PostgreSQL schema and migrations

- Finalize relational entities and relationships.

- Establish schema ownership and naming.

- Implement database mappings.

- Introduce migrations incrementally.

- Add repository implementations and persistence tests.

### Milestone 3 — Parquet storage engine

- Define storage interfaces and dataset layout.

- Implement raw and normalized storage zones.

- Implement safe writes and publication.

- Record schema versions, checksums, and dataset metadata.

- Add read and integrity-check operations.

### Milestone 4 — Persistence integration

- Integrate repositories with application workflows.

- Integrate ingestion records and dataset catalog publication.

- Persist validation reports and quality findings.

- Implement failure recovery and idempotent behavior.

### Milestone 5 — Integration testing

- Test PostgreSQL persistence.

- Test Parquet storage and retrieval.

- Test dataset publication and failure recovery.

- Test lineage and versioning.

- Test duplicate ingestion and retry behavior.

- Test consistency between the catalog and stored files.

Each milestone must be reviewed and tested before proceeding to the next.

## 11. Consequences

### Positive consequences

- Structured metadata and time-series observations use storage technologies suited to their respective workloads.

- The service maintains clear ownership of its business data.

- Historical datasets can be versioned, traced, validated, and reproduced.

- Persistence implementations remain replaceable behind application contracts.

- The architecture supports incremental growth without requiring a premature distributed infrastructure deployment.

### Trade-offs and risks

- PostgreSQL and Parquet require explicit consistency and recovery handling.

- Dataset publication requires a controlled workflow.

- Dataset partitioning and catalog design require careful consideration as data volume grows.

- Schema and dataset versioning introduce additional lifecycle management responsibilities.

- Dependency compatibility must be verified before implementation.

## 12. Safety and operational boundaries

The Market Data Service does not execute trades.

The Execution Service remains restricted to research and simulated execution unless a separate architecture decision and explicit authorization establish otherwise.

Live broker connectivity and real-money trading remain disabled by default.

## 13. Acceptance

This architecture decision is accepted as **ADR-004 v1.0**.

Implementation must follow the milestones and boundaries defined in this document. Changes to the accepted architecture that materially alter data ownership, persistence responsibilities, or safety boundaries require review and an appropriate architecture decision update.
