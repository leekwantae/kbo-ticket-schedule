name: KBO 경기정보 갱신 및 Pages 배포

on:
  workflow_dispatch:
  schedule:
    - cron: "17 */4 * * *"
  push:
    branches: ["main"]

permissions:
  contents: write
  pages: write
  id-token: write

concurrency:
  group: kbo-ticket-schedule
  cancel-in-progress: true

jobs:
  update-and-deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}

    steps:
      - name: 저장소 받기
        uses: actions/checkout@v4

      - name: Python 설정
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: 경기정보 갱신
        run: python update.py

      - name: 갱신 데이터 커밋
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data.js data.json
          if git diff --cached --quiet; then
            echo "변경 사항 없음"
          else
            git commit -m "자동 경기정보 갱신"
            git push
          fi

      - name: Pages 설정
        uses: actions/configure-pages@v5

      - name: Pages 파일 업로드
        uses: actions/upload-pages-artifact@v3
        with:
          path: "."

      - name: Pages 배포
        id: deployment
        uses: actions/deploy-pages@v4
