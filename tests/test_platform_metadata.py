from atlas_quant.platform.metadata import (
    ServiceMetadata,
    get_service_metadata,
)


def test_service_metadata_uses_application_settings():
    metadata = get_service_metadata()

    assert metadata.name == "atlas-quant"
    assert metadata.version == "0.1.0"
    assert metadata.environment == "development"


def test_service_metadata_serializes_to_dictionary():
    metadata = ServiceMetadata(
        name="market-data",
        version="1.0.0",
        environment="testing",
    )

    assert metadata.to_dict() == {
        "name": "market-data",
        "version": "1.0.0",
        "environment": "testing",
    }


def test_service_metadata_is_immutable():
    metadata = ServiceMetadata(
        name="market-data",
        version="1.0.0",
        environment="testing",
    )

    try:
        metadata.name = "research"
    except AttributeError:
        pass
    else:
        raise AssertionError("ServiceMetadata should be immutable")