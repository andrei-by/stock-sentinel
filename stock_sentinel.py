"""
StockSentinel — утилита для автоматической проверки наличия товаров
в интернет-магазинах и отправки уведомлений при появлении в наличии.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

__version__ = "0.1.0"
Status = Literal["IN_STOCK", "OUT_OF_STOCK", "UNKNOWN"]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3


# --- Config ---


def _expand_env_vars(value: Any) -> Any:
    """Подставляет переменные окружения вида ${VAR}."""
    if isinstance(value, str):
        return re.sub(
            r"\$\{(\w+)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def load_config(path: str | Path = "config.yaml", expand_env: bool = True) -> dict:
    """Загружает конфигурацию из YAML файла."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Конфиг не найден: {path}")

    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if expand_env:
        config = _expand_env_vars(config or {})

    # Подмешиваем секреты из secrets.yaml (если есть)
    secrets_path = path.parent / "secrets.yaml"
    if secrets_path.exists():
        with open(secrets_path, encoding="utf-8") as f:
            secrets = yaml.safe_load(f)
        if secrets and "telegram" in secrets:
            if "notifications" not in config:
                config["notifications"] = {}
            if "telegram" not in config["notifications"]:
                config["notifications"]["telegram"] = {}
            config["notifications"]["telegram"].update(secrets["telegram"])

    return config or {}


# --- State ---


def get_state_path(config_path: Path | None = None) -> Path:
    """Путь к файлу состояния."""
    if config_path:
        return config_path.parent / "stock_sentinel_state.json"
    return Path("stock_sentinel_state.json")


def load_state(config_path: Path | None = None) -> dict:
    """Загружает сохранённое состояние."""
    path = get_state_path(config_path)
    if not path.exists():
        return {}

    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, config_path: Path | None = None) -> None:
    """Сохраняет состояние."""
    path = get_state_path(config_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def get_product_state(state: dict, product_id: str) -> tuple[Status | None, str | None]:
    """Возвращает (last_status, last_check_time) для товара."""
    entry = state.get(product_id, {})
    return entry.get("last_status"), entry.get("last_check_time")


def update_product_state(
    state: dict,
    product_id: str,
    status: Status,
    config_path: Path | None = None,
) -> None:
    """Обновляет состояние товара."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state[product_id] = {"last_status": status, "last_check_time": now}
    save_state(state, config_path)


# --- Checker ---


def create_session(config: dict) -> requests.Session:
    """Создаёт сессию с retry и user-agent."""
    http_config = config.get("http", {})
    user_agent = http_config.get("user_agent", DEFAULT_USER_AGENT)
    retries = http_config.get("retries", DEFAULT_RETRIES)

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    retry_strategy = Retry(
        total=retries,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def fetch_page(session: requests.Session, url: str, config: dict) -> str:
    """Загружает страницу и возвращает её текст."""
    timeout = config.get("http", {}).get("timeout", DEFAULT_TIMEOUT)
    logging.info("page fetched: %s", url)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def check_text_rule(page_text: str, rule: dict) -> bool:
    """
    Проверяет правило на тексте страницы.
    type: text_not_contains — товар в наличии, если value НЕ найден
    type: text_contains — товар в наличии, если value найден
    """
    rule_type = rule.get("type", "text_not_contains")
    value = rule.get("value", "")

    if rule_type == "text_not_contains":
        in_stock = value.lower() not in page_text.lower()
    elif rule_type == "text_contains":
        in_stock = value.lower() in page_text.lower()
    else:
        logging.warning("Unknown rule type: %s, assuming text_not_contains", rule_type)
        in_stock = value.lower() not in page_text.lower()

    logging.info("rule result: type=%s value=%r in_stock=%s", rule_type, value, in_stock)
    return in_stock


def check_product(
    session: requests.Session,
    product: dict,
    config: dict,
) -> Status:
    """Проверяет наличие товара и возвращает статус."""
    url = product["url"]
    check_config = product.get("check", {})

    if check_config.get("type", "text_not_contains") in (
        "text_not_contains",
        "text_contains",
    ):
        page_text = fetch_page(session, url, config)
        in_stock = check_text_rule(page_text, check_config)
    else:
        logging.warning("Unsupported check type, defaulting to OUT_OF_STOCK")
        return "OUT_OF_STOCK"

    return "IN_STOCK" if in_stock else "OUT_OF_STOCK"


# --- Notifier ---


def send_telegram(config: dict, product: dict, status: str, url: str) -> bool:
    """Отправляет уведомление в Telegram."""
    telegram_config = config.get("notifications", {}).get("telegram", {})
    token = telegram_config.get("token")
    chat_id = telegram_config.get("chat_id")

    if not token or not chat_id:
        logging.warning("Telegram не настроен: отсутствует token или chat_id")
        return False

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    # HTML чтобы не ломать разбор из-за _ * в статусах и URL
    text = (
        "🔔 <b>StockSentinel Alert</b>\n\n"
        f"<b>Product:</b> {product['name']}\n"
        f"<b>Status:</b> {status}\n"
        f"<b>URL:</b> {url}\n"
        f"<b>Time:</b> {now}"
    )

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": str(chat_id).strip(),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(api_url, json=payload, timeout=10)
        if not response.ok:
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            desc = body.get("description", response.text)
            logging.error("Telegram API error %s: %s", response.status_code, desc)
            return False
        logging.info("notification sent: Telegram")
        return True
    except requests.RequestException as e:
        logging.error("Failed to send Telegram notification: %s", e)
        return False


# --- CLI ---


def _product_id(product: dict) -> str:
    return f"{product['name']}:{product['url']}"


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=level,
    )


def cmd_check(args: argparse.Namespace) -> int:
    """Однократная проверка всех товаров."""
    config_path = Path(args.config)
    config = load_config(config_path)
    products = [p for p in config.get("products", []) if p.get("enabled", True)]

    if not products:
        print("Нет товаров для проверки.")
        return 0

    state = load_state(config_path)
    session = create_session(config)

    for product in products:
        name = product["name"]
        url = product["url"]
        logging.info("check started: %s", name)

        try:
            status = check_product(session, product, config)
        except Exception as e:
            logging.error("check failed for %s: %s", name, e)
            continue

        product_id = _product_id(product)
        last_status, _ = get_product_state(state, product_id)

        if last_status is not None and last_status == "OUT_OF_STOCK" and status == "IN_STOCK":
            logging.info("Status changed: OUT_OF_STOCK -> IN_STOCK for %s", name)
            send_telegram(config, product, status, url)

        update_product_state(state, product_id, status, config_path)
        print(f"  {name}: {status}")

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Список товаров."""
    config_path = Path(args.config)
    config = load_config(config_path)
    products = config.get("products", [])
    state = load_state(config_path)

    if not products:
        print("Список товаров пуст.")
        return 0

    for p in products:
        enabled = "+" if p.get("enabled", True) else "-"
        pid = _product_id(p)
        last_status, last_time = get_product_state(state, pid)
        status_str = last_status or "-"
        time_str = last_time or "-"
        print(f"  [{enabled}] {p['name']}")
        print(f"      URL: {p['url']}")
        print(f"      Last: {status_str} at {time_str}")
        print()
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """Добавление товара."""
    config_path = Path(args.config)
    config = load_config(config_path, expand_env=False)

    new_product = {
        "name": args.name,
        "url": args.url,
        "enabled": True,
        "check": {"type": "text_not_contains", "value": args.text or "Out of stock"},
    }

    if "products" not in config:
        config["products"] = []
    config["products"].append(new_product)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"Добавлен товар: {args.name}")
    return 0


def cmd_test_notify(args: argparse.Namespace) -> int:
    """Отправляет тестовое сообщение в Telegram (проверка настроек)."""
    config_path = Path(args.config)
    config = load_config(config_path)
    product = {"name": "Test", "url": "https://example.com"}
    url = "https://example.com"
    status = "IN_STOCK (test)"
    if send_telegram(config, product, status, url):
        print("Тестовое сообщение отправлено в Telegram.")
        return 0
    print("Не удалось отправить. Проверьте token и chat_id в secrets.yaml или переменные окружения.")
    return 1


def cmd_remove(args: argparse.Namespace) -> int:
    """Удаление товара."""
    config_path = Path(args.config)
    config = load_config(config_path, expand_env=False)
    products = config.get("products", [])

    before = len(products)
    config["products"] = [p for p in products if p["name"] != args.name]
    after = len(config["products"])

    if before == after:
        print(f"Товар не найден: {args.name}")
        return 1

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"Удалён товар: {args.name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="stock-sentinel",
        description="Мониторинг наличия товаров в интернет-магазинах",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Подробный вывод")
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="Путь к конфигу (default: config.yaml)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check", help="Однократная проверка всех товаров").set_defaults(func=cmd_check)
    subparsers.add_parser("list", help="Список товаров").set_defaults(func=cmd_list)
    subparsers.add_parser("test-notify", help="Отправить тестовое уведомление в Telegram").set_defaults(func=cmd_test_notify)

    add_parser = subparsers.add_parser("add", help="Добавить товар")
    add_parser.add_argument("name", help="Название товара")
    add_parser.add_argument("url", help="URL страницы")
    add_parser.add_argument("-t", "--text", help="Текст отсутствия (default: Out of stock)")
    add_parser.set_defaults(func=cmd_add)

    remove_parser = subparsers.add_parser("remove", help="Удалить товар")
    remove_parser.add_argument("name", help="Название товара")
    remove_parser.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    _setup_logging(args.verbose)

    try:
        return args.func(args)
    except FileNotFoundError as e:
        logging.error("%s", e)
        return 1
    except Exception as e:
        logging.exception("Ошибка: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
