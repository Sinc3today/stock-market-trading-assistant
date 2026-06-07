import os, sys, ast
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _src(path):
    with open(os.path.join(os.path.dirname(__file__), "..", path)) as f:
        return f.read()


def test_discord_bot_module_is_deleted():
    path = os.path.join(os.path.dirname(__file__), "..", "alerts", "discord_bot.py")
    assert not os.path.exists(path), "alerts/discord_bot.py must stay deleted (Discord removed)"


def test_main_does_not_import_or_start_discord_bot():
    src = _src("main.py")
    assert "discord_bot" not in src, "main.py must not import alerts.discord_bot"
    assert "start_discord" not in src, "main.py must not start the Discord bot"


def test_main_constructs_pushover_only_notifier():
    src = _src("main.py")
    assert "discord_alert_fn" not in src and "discord_message_fn" not in src
    assert "set_play_fn(notifier.play)" in src
    assert "play_fn=notifier.play" in src


def test_news_scanner_does_not_import_discord_bot():
    src = _src("scanners/news_scanner.py")
    assert "from alerts.discord_bot import bot" not in src


def test_economic_scanner_does_not_import_discord_bot():
    src = _src("scanners/economic_scanner.py")
    assert "discord_bot" not in src, \
        "scanners/economic_scanner.py must not import alerts.discord_bot"
