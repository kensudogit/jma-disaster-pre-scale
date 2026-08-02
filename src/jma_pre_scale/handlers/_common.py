"""ハンドラ共通の初期化。Lambda のコールドスタート時に1度だけ実行する。"""
from __future__ import annotations

import logging
import os

from ..config import Config, load
from ..controller import AwsClients, ScalingController
from ..notifier import Notifier
from ..state import StateStore, build_store

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

_config: Config | None = None
_store: StateStore | None = None
_notifier: Notifier | None = None
_controller: ScalingController | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load()
    return _config


def get_store() -> StateStore:
    global _store
    if _store is None:
        _store = build_store(get_config())
    return _store


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier.build(get_config())
    return _notifier


def get_controller() -> ScalingController:
    global _controller
    if _controller is None:
        cfg = get_config()
        clients = AwsClients() if cfg.dry_run else AwsClients.build(cfg.region)
        _controller = ScalingController(cfg, clients)
    return _controller


def reset_for_tests() -> None:
    global _config, _store, _notifier, _controller
    _config = _store = _notifier = _controller = None
