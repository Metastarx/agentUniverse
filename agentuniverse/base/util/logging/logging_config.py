# !/usr/bin/env python3
# -*- coding:utf-8 -*-

# @Time    : 2024/3/13 11:28
# @Author  : fanen.lhy
# @Email   : fanen.lhy@antgroup.com
# @FileName: logging_config.py

from typing import Optional, List, Dict

import tomli
from agentuniverse.base.config.configer import Configer

def _load_toml_file(path: str) -> dict:
    """Load the toml file.

    Args:
        path(str): the path of the toml file
    Returns:
        dict: the value of the toml file
    """
    with open(path, 'rb') as f:
        config_data = tomli.load(f)
    return config_data


class LoggingConfig(object):
    """Config class of logging utils."""
    log_format: str = ("<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
                       "| <level>{level: <8}</level> "
                       "| {extra[context_prefix]} "
                       "| <cyan>{name}</cyan>"
                       ":<cyan>{function}</cyan>"
                       ":<cyan>{line}</cyan> "
                       "| <level>{message}</level>")
    log_level: str = "INFO"
    log_extend_module_list: List[str] = ["sls_log"]
    log_extend_module_switch: Dict[str, bool] = {}
    log_path: Optional[str] = None
    log_rotation: str = "10 MB"
    log_retention: str = "3 days"
    log_compression: str = "zip"

    # Aliyun sls configs.
    sls_endpoint: str = ""
    sls_project: str = ""
    sls_log_store: str = ""
    access_key_id: str = ""
    access_key_secret: str = ""
    sls_log_queue_max_size: int = 1000
    sls_log_send_interval: float = 3.0

    def __init__(self, config_path: Optional[str] = None):
        """Initialize the log config using config file if specified, otherwise
        use default value.

        Args:
            config_path(str):
                the path of the toml file, ``None`` or an empty string means
                the default configuration will be used.
        """
        self.__config = None
        if not config_path:
            # ``config_path`` is optional: ``AgentUniverse.start`` passes
            # ``None`` whenever the main config file does not declare
            # ``log_config_path``.  Passing the raw value straight to
            # ``Configer.load_by_path`` used to fail with an opaque
            # ``AttributeError`` raised by ``str.split``, so fall back to the
            # documented defaults and disable every extension module instead.
            print("no log config file specified, use default config")
            self._disable_extend_modules()
            return
        try:
            self.__config = Configer().load_by_path(config_path).value['LOG_CONFIG']
        except (FileNotFoundError, TypeError):
            print("can't find log config file, use default config")
            self._disable_extend_modules()
            return
        except (tomli.TOMLDecodeError, KeyError):
            print("log config file isn't a valid toml, use default config.")
            self._disable_extend_modules()
            return

        for log_module in LoggingConfig.log_extend_module_list:
            switch = self._get_config_or_default("EXTEND_MODULE",
                                                 log_module)
            if switch and switch.lower() == 'true':
                LoggingConfig.log_extend_module_switch[log_module] = True
            else:
                LoggingConfig.log_extend_module_switch[log_module] = False

        log_level = self._get_config_or_default("BASIC_CONFIG",
                                                "log_level")
        if log_level:
            LoggingConfig.log_level = log_level.upper()

        log_path = self._get_config_or_default("BASIC_CONFIG",
                                               "log_path")
        if log_path:
            LoggingConfig.log_path = log_path

        log_rotation = self._get_config_or_default("BASIC_CONFIG",
                                                   "log_rotation")
        if log_rotation:
            LoggingConfig.log_rotation = log_rotation

        log_retention = self._get_config_or_default("BASIC_CONFIG",
                                                    "log_retention")
        if log_retention:
            LoggingConfig.log_retention = log_retention

        log_compress = self._get_config_or_default("BASIC_CONFIG",
                                                   "log_compression")
        if log_compress is not None:
            LoggingConfig.log_compression = log_compress

        # Read sls config when sls extend module come into effect.
        if LoggingConfig.log_extend_module_switch["sls_log"]:
            LoggingConfig.sls_endpoint = self._get_config_or_default(
                "ALIYUN_SLS_CONFIG", "sls_endpoint")
            LoggingConfig.sls_project = self._get_config_or_default(
                "ALIYUN_SLS_CONFIG", "sls_project")
            LoggingConfig.sls_log_store = self._get_config_or_default(
                "ALIYUN_SLS_CONFIG", "sls_log_store")
            LoggingConfig.access_key_id = self._get_config_or_default(
                "ALIYUN_SLS_CONFIG", "access_key_id")
            LoggingConfig.access_key_secret = self._get_config_or_default(
                "ALIYUN_SLS_CONFIG", "access_key_secret")
            LoggingConfig.sls_log_queue_max_size = int(
                self._get_config_or_default("ALIYUN_SLS_CONFIG",
                                            "sls_log_queue_max_size"))
            LoggingConfig.sls_log_send_interval = float(
                self._get_config_or_default("ALIYUN_SLS_CONFIG",
                                            "sls_log_send_interval"))

    @classmethod
    def _disable_extend_modules(cls):
        """Disable every optional log extension module.

        The documented default configuration only covers the built-in stdout
        and file handlers, therefore every extension module (for example the
        Aliyun SLS sink) must be switched off explicitly when no usable
        configuration file is available.  Keeping the switch map in sync with
        ``log_extend_module_list`` also prevents a stale ``True`` value from a
        previously loaded config file leaking into the default setup.
        """
        for log_module in cls.log_extend_module_list:
            cls.log_extend_module_switch[log_module] = False

    def _get_config_or_default(self, section, key, default_value=None):
        """Get config attribute from toml data, return default_value if no such
        attribute."""
        try:
            return self.__config[section][key]
        except (KeyError, TypeError):
            return default_value


def init_log_config(config_path: Optional[str] = None):
    LoggingConfig(config_path)
