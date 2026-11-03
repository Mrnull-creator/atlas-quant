import logging

from atlas_quant.platform import logging as platform_logging


def test_configure_logging_outputs_service_metadata(capsys, monkeypatch):
    root_logger = logging.getLogger()

    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level

    try:
        for handler in original_handlers:
            root_logger.removeHandler(handler)

        monkeypatch.setattr(platform_logging, "_CONFIGURED", False)

        platform_logging.configure_logging()

        logger = platform_logging.get_logger("atlas_quant.test")
        logger.info("Configuration test message")

        output = capsys.readouterr().out

        assert "INFO" in output
        assert "atlas-quant" in output
        assert "development" in output
        assert "atlas_quant.test" in output
        assert "Configuration test message" in output

    finally:
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            handler.close()

        root_logger.handlers[:] = original_handlers
        root_logger.setLevel(original_level)


def test_get_logger_returns_named_logger():
    logger = platform_logging.get_logger("atlas_quant.research")

    assert isinstance(logger, logging.Logger)
    assert logger.name == "atlas_quant.research"