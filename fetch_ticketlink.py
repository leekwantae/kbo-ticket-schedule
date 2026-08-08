from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

OUT_FILE = Path("ticketlink_games.json")
DEBUG_DIR = Path("debug_ticketlink")

# 1차 테스트: LG만 확인
TEAMS = [
    {
        "team": "LG",
        "teamId": 59,
        "categoryId": 137,
        "url": "https://www.ticketlink.co.kr/sports/137/59",
    }
]

DATE_RE = re.compile(r"(\d{2})\.(\d{2})\([월화수목금토일]\)")
TIME_RE = re.compile(r"\b(\d{2}:\d{2})\b")
OPEN_RE = re.compile(
    r"(\d{4})\.(\d{2})\.(\d{2})\([월화수목금토일]\)\s+(\d{2}:\d{2})\s+오픈예정"
)


def normalize_team_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"^홈", "", name)
    aliases = {
        "LG": "LG",
        "LG트윈스": "LG",
        "kt": "KT",
        "kt wiz": "KT",
        "삼성": "삼성",
        "삼성 라이온즈": "삼성",
        "한화": "한화",
        "한화이글스": "한화",
        "KIA": "KIA",
        "KIA 타이거즈": "KIA",
        "SSG": "SSG",
        "NC": "NC",
        "두산": "두산",
        "키움": "키움",
        "롯데": "롯데",
    }
    return aliases.get(name, name)


def parse_game(li, page_year: int, team_meta: dict) -> dict | None:
    team_el = li.locator(".team_name")
    place_el = li.locator(".place")
    reserve_el = li.locator(".btn_reserve")

    if team_el.count() == 0 or place_el.count() == 0 or reserve_el.count() == 0:
        return None

    text = li.inner_text().strip()
    team_text = re.sub(r"\s+", " ", team_el.inner_text().strip())
    place = place_el.inner_text().strip()
    reserve_text = re.sub(r"\s+", " ", reserve_el.inner_text().strip())

    dm = DATE_RE.search(text)
    tm = TIME_RE.search(text)
    if not dm or not tm:
        return None

    month = int(dm.group(1))
    day = int(dm.group(2))
    game_date = f"{page_year:04d}-{month:02d}-{day:02d}"

    # 예: "SSG vs 홈LG"
    home_team = ""
    away_team = ""
    if " vs " in team_text:
        left, right = [x.strip() for x in team_text.split(" vs ", 1)]
        if right.startswith("홈"):
            away_team = normalize_team_name(left)
            home_team = normalize_team_name(right)
        elif left.startswith("홈"):
            home_team = normalize_team_name(left)
            away_team = normalize_team_name(right)
        else:
            away_team = normalize_team_name(left)
            home_team = normalize_team_name(right)

    is_plan = "plan_sale" in (reserve_el.get_attribute("class") or "")
    if is_plan:
        status = "예매예정"
    elif "예매하기" in reserve_text:
        status = "예매중"
    else:
        status = reserve_text or "알 수 없음"

    open_datetime = ""
    om = OPEN_RE.search(reserve_text)
    if om:
        open_datetime = (
            f"{om.group(1)}-{om.group(2)}-{om.group(3)}T{om.group(4)}:00+09:00"
        )

    return {
        "date": game_date,
        "time": tm.group(1),
        "homeTeam": home_team,
        "awayTeam": away_team,
        "place": place,
        "status": status,
        "openDateText": reserve_text if is_plan else "",
        "openDateTime": open_datetime,
        "platform": "ticketlink",
        "teamId": team_meta["teamId"],
        "categoryId": team_meta["categoryId"],
        "pageUrl": team_meta["url"],
    }


def collect_team(page, team_meta: dict) -> list[dict]:
    url = team_meta["url"]
    print(f"[OPEN] {team_meta['team']} / {url}")

    response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if response:
        print(f"[HTTP] {response.status}")

    # SPA가 경기 목록을 렌더링할 시간을 기다림
    page.wait_for_selector(".team_name", timeout=30000)
    page.wait_for_timeout(2500)

    # /500 이동 여부 확인
    if page.url.rstrip("/").endswith("/500"):
        raise RuntimeError("Ticketlink가 /500 오류 페이지로 이동했습니다.")

    page_year = datetime.now().year

    games = []
    seen = set()

    for li in page.locator("li").all():
        try:
            game = parse_game(li, page_year, team_meta)
        except Exception:
            continue

        if not game:
            continue

        key = (
            game["date"],
            game["time"],
            game["homeTeam"],
            game["awayTeam"],
            game["place"],
        )
        if key in seen:
            continue

        seen.add(key)
        games.append(game)

    games.sort(key=lambda x: (x["date"], x["time"]))
    print(f"[FOUND] {team_meta['team']}: {len(games)} games")
    return games


def main():
    DEBUG_DIR.mkdir(exist_ok=True)
    all_games = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        for meta in TEAMS:
            try:
                games = collect_team(page, meta)
                all_games.extend(games)
            except PlaywrightTimeoutError as e:
                print(f"[TIMEOUT] {meta['team']}: {e}")
                page.screenshot(
                    path=str(DEBUG_DIR / f"{meta['team']}_timeout.png"),
                    full_page=True,
                )
                (DEBUG_DIR / f"{meta['team']}_timeout.html").write_text(
                    page.content(), encoding="utf-8"
                )
            except Exception as e:
                print(f"[ERROR] {meta['team']}: {e}")
                try:
                    page.screenshot(
                        path=str(DEBUG_DIR / f"{meta['team']}_error.png"),
                        full_page=True,
                    )
                    (DEBUG_DIR / f"{meta['team']}_error.html").write_text(
                        page.content(), encoding="utf-8"
                    )
                except Exception:
                    pass

        browser.close()

    OUT_FILE.write_text(
        json.dumps(all_games, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[SAVE] {OUT_FILE} / {len(all_games)} games")

    # LG 테스트에서 0건이면 workflow를 실패시켜 바로 알 수 있게 함
    if not all_games:
        raise SystemExit("Ticketlink 경기 수집 결과가 0건입니다. debug_ticketlink 파일을 확인하세요.")


if __name__ == "__main__":
    main()
