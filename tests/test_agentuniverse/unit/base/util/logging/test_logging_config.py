# !/usr/bin/env python3
# -*- coding:utf-8 -*-

# @Time    : 2024/3/13 15:50
# @Author  : fanen.lhy
# @Email   : fanen.lhy@antgroup.com
# @FileName: test_logging_config.py

import os
import time

import loguru
import pytest

from agentuniverse.base.config.configer import Configer
from agentuniverse.base.util.logging.logging_config import (
    LoggingConfig,
    init_log_config,
)
from agentuniverse.base.util.logging.logging_util import (
    LOGGER,
    LOG_FILE_PREFIX,
    init_loggers,
)

# ``log_config.toml`` lives next to the agent unit tests and enables no
# extension module, which makes it a convenient fixture for the happy path.
_FIXTURE_CONFIG_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "agent", "log_config.toml"))

# Class level attributes that ``LoggingConfig`` mutates while parsing a file.
_CONFIG_ATTRS = (
    "log_level",
    "log_path",
    "log_rotation",
    "log_retention",
    "log_compression",
    "sls_endpoint",
    "sls_project",
    "sls_log_store",
    "access_key_id",
    "access_key_secret",
    "sls_log_queue_max_size",
    "sls_log_send_interval",
)


@pytest.fixture()
def restore_logging_config():
    """Restore the class level state mutated by ``LoggingConfig``.

    ``LoggingConfig`` keeps its parsed values on the class itself, so a test
    that loads a configuration file would otherwise leak state (log level,
    log path, extension switches, ...) into every following test.
    """
    snapshot = {name: getattr(LoggingConfig, name) for name in _CONFIG_ATTRS}
    switch_snapshot = dict(LoggingConfig.log_extend_module_switch)
    yield
    for name, value in snapshot.items():
        setattr(LoggingConfig, name, value)
    LoggingConfig.log_extend_module_switch.clear()
    LoggingConfig.log_extend_module_switch.update(switch_snapshot)


def _assert_all_extend_modules_disabled():
    """Every optional extension module has to be switched off."""
    assert LoggingConfig.log_extend_module_switch
    for log_module in LoggingConfig.log_extend_module_list:
        assert LoggingConfig.log_extend_module_switch[log_module] is False


def test_logging_config_without_path_uses_defaults(restore_logging_config):
    """``LoggingConfig()`` must not raise when no path is supplied.

    Regression test for the ``AttributeError: 'NoneType' object has no
    attribute 'split'`` that ``Configer.load_by_path(None)`` used to raise.
    """
    LoggingConfig()
    _assert_all_extend_modules_disabled()


def test_logging_config_empty_path_uses_defaults(restore_logging_config):
    """An empty path is equivalent to no path at all."""
    LoggingConfig("")
    _assert_all_extend_modules_disabled()


def test_logging_config_missing_file_uses_defaults(restore_logging_config,
                                                   tmp_path):
    """A path pointing to a file that does not exist falls back to defaults."""
    LoggingConfig(str(tmp_path / "not_created" / "log_config.toml"))
    _assert_all_extend_modules_disabled()


def test_logging_config_invalid_toml_uses_defaults(restore_logging_config,
                                                   tmp_path):
    """A corrupt toml file must not prevent the loggers from starting."""
    invalid_config = tmp_path / "invalid.toml"
    invalid_config.write_text("this is not valid toml", encoding="utf-8")

    LoggingConfig(str(invalid_config))
    _assert_all_extend_modules_disabled()


def test_logging_config_valid_file_overrides_defaults(restore_logging_config):
    """A valid file is still parsed and its values take effect."""
    assert os.path.exists(_FIXTURE_CONFIG_PATH), _FIXTURE_CONFIG_PATH

    LoggingConfig(_FIXTURE_CONFIG_PATH)

    assert LoggingConfig.log_level == "INFO"
    assert LoggingConfig.log_path == "./.test_log_dir"
    assert LoggingConfig.log_rotation == "100 MB"
    assert LoggingConfig.log_retention == "7 days"
    assert LoggingConfig.log_extend_module_switch["sls_log"] is False


def test_init_log_config_without_path(restore_logging_config):
    """``init_log_config`` keeps working without an explicit config path."""
    init_log_config()

    assert "sls_log" in LoggingConfig.log_extend_module_switch
    assert LoggingConfig.log_extend_module_switch["sls_log"] is False


def test_init_loggers_without_path(restore_logging_config, tmp_path):
    """``init_loggers()`` installs the default handlers without a config file.

    The standard and error file handlers have to be registered and write into
    the configured log directory even though no log config file was provided.
    """
    log_dir = tmp_path / "logs"
    LoggingConfig.log_path = str(log_dir)

    init_loggers()
    try:
        LOGGER.info("default config info log")
        LOGGER.error("default config error log")
        # The handlers use ``enqueue=True``, give the sink thread a moment to
        # flush the queued records before reading the files back.
        time.sleep(0.3)

        all_log_file = log_dir / f"{LOG_FILE_PREFIX}_all.log"
        error_log_file = log_dir / f"{LOG_FILE_PREFIX}_error.log"
        assert all_log_file.exists()
        assert error_log_file.exists()

        all_log_content = all_log_file.read_text(encoding="utf-8")
        assert "default config info log" in all_log_content
        assert "default config error log" in all_log_content
        assert "default config error log" in error_log_file.read_text(
            encoding="utf-8")
    finally:
        # Drop the handlers created above so that later tests are not affected
        # by the temporary log directory used here.
        loguru.logger.remove()


def test_configer_load_by_path_rejects_none():
    """A missing path raises a descriptive error instead of AttributeError."""
    with pytest.raises(ValueError, match="non-empty string"):
        Configer().load_by_path(None)


def test_configer_load_by_path_rejects_empty_string():
    with pytest.raises(ValueError, match="non-empty string"):
        Configer().load_by_path("")


def test_configer_load_by_path_rejects_blank_string():
    with pytest.raises(ValueError, match="non-empty string"):
        Configer().load_by_path("   ")


def test_configer_load_by_path_rejects_non_string():
    with pytest.raises(ValueError, match="non-empty string"):
        Configer().load_by_path(123)


def test_configer_load_by_path_rejects_unsupported_format(tmp_path):
    unsupported_config = tmp_path / "log_config.ini"
    unsupported_config.write_text("[LOG_CONFIG]", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported file format"):
        Configer().load_by_path(str(unsupported_config))


def test_configer_load_by_path_still_loads_valid_toml():
    """The new guard must not break loading a well formed configuration."""
    configer = Configer().load_by_path(_FIXTURE_CONFIG_PATH)

    assert configer.value["LOG_CONFIG"]["BASIC_CONFIG"]["log_level"] == "INFO"
