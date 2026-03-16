# StockSentinel

Утилита для автоматической проверки наличия товаров в интернет-магазинах и отправки уведомлений при появлении товара в наличии.

## Запуск

```bash
# Установите зависимости
pip install -r requirements.txt

# Запуск
python stock_sentinel.py check
python stock_sentinel.py list
```

## Конфигурация

1. Скопируйте `config.example.yaml` в `config.yaml`
2. Добавьте товары и настройте Telegram:

```yaml
products:
  - name: RTX4090
    url: https://shop.com/rtx4090
    enabled: true
    check:
      type: text_not_contains
      value: "Out of stock"

notifications:
  telegram:
    token: ${TELEGRAM_TOKEN}
    chat_id: ${TELEGRAM_CHAT_ID}
```

3. Токены Telegram — один из способов:
   - **Переменные окружения:** `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
   - **Файл secrets.yaml:** скопируйте `secrets.example.yaml` в `secrets.yaml`, заполните и добавьте в `.gitignore`

## CLI команды

| Команда | Описание |
|---------|----------|
| `python stock_sentinel.py check` | Однократная проверка всех товаров |
| `python stock_sentinel.py list` | Список товаров и их статусов |
| `python stock_sentinel.py add NAME URL` | Добавить товар |
| `python stock_sentinel.py remove NAME` | Удалить товар |

### Примеры

```bash
# Проверка
python stock_sentinel.py check

# Добавление товара
python stock_sentinel.py add "RTX 4090" "https://shop.com/rtx4090" -t "Нет в наличии"

# Список
python stock_sentinel.py list
```

## Правила проверки

- **text_not_contains** — товар в наличии, если на странице НЕТ указанного текста (например, "Out of stock")
- **text_contains** — товар в наличии, если на странице ЕСТЬ указанный текст

## GitHub Actions

```yaml
name: Stock Check
on:
  schedule:
    - cron: '0 */6 * * *'  # каждые 6 часов
  workflow_dispatch:
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - name: Cache state
        uses: actions/cache@v4
        with:
          path: stock_sentinel_state.json
          key: stock-sentinel-state
      - run: python stock_sentinel.py check
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

Состояние проверок (`stock_sentinel_state.json`) сохраняется между запусками через кэш GitHub Actions, поэтому уведомление о появлении товара придёт после смены статуса между двумя прогонами.

## Требования

- Python 3.9+
- requests, PyYAML
