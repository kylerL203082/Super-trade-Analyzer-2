name: Update TradeForge Rankings

on:
  workflow_dispatch:

  schedule:
    - cron: "15 13 * * 2"
    - cron: "15 16 * * 5"
    - cron: "30 14 * * 0"

permissions:
  contents: write

concurrency:
  group: tradeforge-rankings
  cancel-in-progress: true

jobs:
  update-rankings:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout TradeForge
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Confirm files exist
        run: |
          echo "Repository files:"
          find . -maxdepth 3 -type f | sort

      - name: Build TradeForge data feed
        run: |
          python scripts/update_tradeforge_data.py

      - name: Validate TradeForge feed
        run: |
          python - <<'PY'
          import json
          from pathlib import Path

          path = Path("tradeforge-data.json")

          if not path.exists():
              raise SystemExit("tradeforge-data.json was not created")

          data = json.loads(path.read_text())

          if data["meta"]["baselinePlayers"] < 300:
              raise SystemExit("Feed did not contain the expected player database")

          if not isinstance(data["players"], dict):
              raise SystemExit("players section is not valid")

          print("Feed validation passed")
          print(data["meta"])
          PY

      - name: Commit updated rankings
        run: |
          if git diff --quiet -- tradeforge-data.json; then
            echo "No TradeForge data changes to commit."
            exit 0
          fi

          git config user.name "TradeForge Bot"
          git config user.email "tradeforge-bot@users.noreply.github.com"

          git add tradeforge-data.json
          git commit -m "Update TradeForge rankings"
          git push
