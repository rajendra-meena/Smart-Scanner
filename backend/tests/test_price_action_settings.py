import sqlite3
from threading import RLock

import pytest

from app.core.price_action_service import PriceActionService


class Engine:
    config = {}


def service(tmp_path):
    instance = PriceActionService.__new__(PriceActionService)
    instance.lock = RLock()
    instance.db_path = tmp_path / "price_action.sqlite3"
    instance.config = {**PriceActionService.DEFAULTS, "max_positions": 9, "stop_loss_percent": 25.0}
    instance.engine = Engine()
    instance.running = False
    with sqlite3.connect(instance.db_path) as db:
        db.execute("CREATE TABLE price_action_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO price_action_settings VALUES ('obsolete_setting', 'true')")
    return instance


def test_reset_settings_restores_and_persists_all_defaults(tmp_path):
    instance = service(tmp_path)
    result = instance.reset_settings()

    assert result["config"] == PriceActionService.DEFAULTS
    assert instance.engine.config == PriceActionService.DEFAULTS
    with sqlite3.connect(instance.db_path) as db:
        saved = dict(db.execute("SELECT key, value FROM price_action_settings"))
    assert set(saved) == set(PriceActionService.DEFAULTS)
    assert saved["stop_loss_percent"] == "15.0"


def test_reset_settings_is_blocked_while_strategy_running(tmp_path):
    instance = service(tmp_path)
    instance.running = True

    with pytest.raises(RuntimeError, match="Stop Price Action Intraday"):
        instance.reset_settings()
