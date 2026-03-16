# StockSentinel

Utility to monitor product availability on e-commerce sites and get notified when items come back in stock.

## Run

```bash
pip install -r requirements.txt

python stock_sentinel.py check
python stock_sentinel.py list
```

## Configuration

**Local:** Copy `config.example.yaml` to `config.yaml` (the file is in `.gitignore`). Add products and configure Telegram:

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

**Telegram credentials** — use either:
- **Environment variables:** `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
- **File `secrets.yaml`:** copy `secrets.example.yaml` to `secrets.yaml`, fill in values (keep `secrets.yaml` in `.gitignore`)

## CLI

| Command | Description |
|---------|-------------|
| `python stock_sentinel.py check` | One-off check of all products |
| `python stock_sentinel.py list` | List products and last status |
| `python stock_sentinel.py add NAME URL` | Add a product |
| `python stock_sentinel.py remove NAME` | Remove a product |
| `python stock_sentinel.py test-notify` | Send a test Telegram message |

Use `--quiet` (or `-q`) to log only warnings and errors (e.g. in GitHub Actions).

### Examples

```bash
python stock_sentinel.py check
python stock_sentinel.py add "RTX 4090" "https://shop.com/rtx4090" -t "Out of stock"
python stock_sentinel.py list
```

## Check rules

- **text_not_contains** — in stock when the given text is NOT on the page (e.g. "Out of stock")
- **text_contains** — in stock when the given text IS on the page

## GitHub Actions

Add these repository secrets (Settings → Secrets and variables → Actions):

| Secret | Description |
|--------|-------------|
| `TELEGRAM_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Chat ID for notifications |
| `CONFIG_YAML` | Full contents of your `config.yaml` (multiline OK) |

On first run the workflow creates `config.yaml` from `CONFIG_YAML`; afterwards config and state are stored in the Actions cache between runs.

```yaml
name: Stock Check
on:
  schedule:
    - cron: '0 */6 * * *'
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
      - name: Restore cache (config + state)
        uses: actions/cache@v4
        with:
          path: |
            config.yaml
            stock_sentinel_state.json
          key: stock-sentinel-data
      - name: Prepare config
        run: |
          if [ ! -f config.yaml ]; then
            echo "$CONFIG_YAML" > config.yaml
          fi
        env:
          CONFIG_YAML: ${{ secrets.CONFIG_YAML }}
      - name: Check stock
        run: python stock_sentinel.py check --quiet
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

Config and check state are persisted in the cache between runs.

### Public repositories: cache and logs

- **Cache:** GitHub Actions cache is **not** visible to other users. Only workflow runs in this repository can read/write the cache; nobody can browse cached files. Your `config.yaml` and state stay private.
- **Logs:** Workflow run **logs are visible** to anyone with read access. For a public repo, that means everyone can see step output (e.g. product names and URLs if the script prints them). **Secrets are masked** in logs (replaced with `***`). Avoid logging sensitive or personal data.

## Requirements

- Python 3.9+
- requests, PyYAML

## License

GPL-3.0. See [LICENSE](LICENSE).
