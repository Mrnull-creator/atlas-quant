
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.domain.dataset_lineage import (
    DatasetLineageRelationship,
    DatasetLineageSourceType,
    DatasetVersionLineage,
)


def test_create_ingestion_lineage():
    target_id = uuid4()
    ingestion_id = uuid4()

    lineage = DatasetVersionLineage(
        target_dataset_version_id=target_id,
        source_type=DatasetLineageSourceType.INGESTION_JOB,
        source_id=ingestion_id,
        relationship=DatasetLineageRelationship.INGESTED_FROM,
    )

    assert lineage.target_dataset_version_id == target_id
    assert lineage.source_id == ingestion_id
    assert lineage.relationship == DatasetLineageRelationship.INGESTED_FROM


def test_create_dataset_derivation_lineage():
    target_id = uuid4()
    source_id = uuid4()

    lineage = DatasetVersionLineage(
        target_dataset_version_id=target_id,
        source_type=DatasetLineageSourceType.DATASET_VERSION,
        source_id=source_id,
        relationship=DatasetLineageRelationship.DERIVED_FROM,
    )

    assert lineage.target_dataset_version_id == target_id
    assert lineage.source_id == source_id
    assert lineage.relationship == DatasetLineageRelationship.DERIVED_FROM


def test_lineage_generates_unique_id():
    common = {
        "target_dataset_version_id": uuid4(),
        "source_type": DatasetLineageSourceType.INGESTION_JOB,
        "source_id": uuid4(),
        "relationship": DatasetLineageRelationship.INGESTED_FROM,
    }

    first = DatasetVersionLineage(**common)
    second = DatasetVersionLineage(**common)

    assert first.lineage_id != second.lineage_id


def test_rejects_non_uuid_identifiers():
    with pytest.raises(TypeError):
        DatasetVersionLineage(
            target_dataset_version_id="target",
            source_type=DatasetLineageSourceType.INGESTION_JOB,
            source_id=uuid4(),
            relationship=DatasetLineageRelationship.INGESTED_FROM,
        )


def test_rejects_incorrect_ingestion_relationship():
    with pytest.raises(ValueError):
        DatasetVersionLineage(
            target_dataset_version_id=uuid4(),
            source_type=DatasetLineageSourceType.INGESTION_JOB,
            source_id=uuid4(),
            relationship=DatasetLineageRelationship.DERIVED_FROM,
        )


def test_rejects_incorrect_dataset_version_relationship():
    with pytest.raises(ValueError):
        DatasetVersionLineage(
            target_dataset_version_id=uuid4(),
            source_type=DatasetLineageSourceType.DATASET_VERSION,
            source_id=uuid4(),
            relationship=DatasetLineageRelationship.INGESTED_FROM,
        )


def test_rejects_self_referencing_dataset_version():
    version_id = uuid4()

    with pytest.raises(ValueError):
        DatasetVersionLineage(
            target_dataset_version_id=version_id,
            source_type=DatasetLineageSourceType.DATASET_VERSION,
            source_id=version_id,
            relationship=DatasetLineageRelationship.DERIVED_FROM,
        )


def test_lineage_is_immutable():
    lineage = DatasetVersionLineage(
        target_dataset_version_id=uuid4(),
        source_type=DatasetLineageSourceType.INGESTION_JOB,
        source_id=uuid4(),
        relationship=DatasetLineageRelationship.INGESTED_FROM,
    )

    with pytest.raises(AttributeError):
        lineage.source_id = uuid4()