from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
KST = timezone(timedelta(hours=9), name="KST")
TICKETLINK_API = "https://mapi.ticketlink.co.kr/mapi/sports/schedules"

HEADERS_JSON = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.ticketlink.co.kr/",
}
HEADERS_HTML = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

def fetch(url: str, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=40) as response:
        return response.read()

def fetch_json(url: str) -> Any:
    return json.loads(fetch(url, HEADERS_JSON).decode("utf-8", errors="replace"))

def ms_to_dt(value: Any):
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, KST)
    except Exception:
        return None

def team_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("teamName") or value.get("teamShortName") or value.get("name") or "").strip()
    return ""

def collect_ticketlink(now: datetime, end: datetime):
    events, status_list = [], []
    for source in CONFIG.get("ticketlink", []):
        params = urllib.parse.urlencode({
            "categoryId": source["categoryId"],
            "teamId": source["teamId"],
            "startDate": now.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
        })
        url = f"{TICKETLINK_API}?{params}"
        status = {"site": "티켓링크", "team": source["team"], "success": False, "count": 0, "message": ""}
        try:
            payload = fetch_json(url)
            schedules = payload.get("data", {}).get("schedules", [])
            if not isinstance(schedules, list):
                raise RuntimeError("data.schedules 배열이 없습니다.")
            for item in schedules:
                game = ms_to_dt(item.get("scheduleDate"))
                if not game:
                    continue
                reserve = ms_to_dt(
                    item.get("reserveOpenDateTime")
                    or item.get("reserveOpenDate")
                    or item.get("reservePreOpenDateTime")
                )
                status_code = str(item.get("reserveButtonStatus") or "").upper()
                schedule_id = str(item.get("scheduleId") or "")
                event = {
                    "id": "TL-" + (schedule_id or f"{source['team']}-{game.isoformat()}"),
                    "site": "티켓링크",
                    "sourceTeam": source["team"],
                    "date": game.strftime("%Y-%m-%d"),
                    "time": game.strftime("%H:%M"),
                    "away": team_name(item.get("awayTeam")),
                    "home": team_name(item.get("homeTeam")),
                    "venue": str(item.get("venueName") or "").strip(),
                    "title": str(item.get("matchTitle") or "").strip(),
                    "bookingOpen": reserve.strftime("%Y-%m-%d %H:%M") if reserve else "",
                    "bookingStatus": "예매중" if status_code == "ON_SALE" else ("예매예정" if reserve else ""),
                    "scheduleId": schedule_id,
                    "productId": str(item.get("productId") or ""),
                    "link": source["pageUrl"],
                }
                events.append(event)
            status["success"] = True
            status["count"] = sum(1 for e in events if e["site"] == "티켓링크" and e["sourceTeam"] == source["team"])
            status["message"] = "API 조회 성공"
        except Exception as exc:
            status["message"] = str(exc)
        status_list.append(status)
    return events, status_list

def decode_next_chunks(page: str) -> str:
    chunks = []
    pattern = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:\\.|[^"\\])*")\]\)', re.DOTALL)
    for match in pattern.finditer(page):
        try:
            chunks.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass
    return page + "\n" + "\n".join(chunks)

def extract_goods_objects(text: str) -> list[dict]:
    text = html.unescape(text)
    decoder, games, seen = json.JSONDecoder(), [], set()
    candidates = [text, text.replace(r'\"', '"').replace(r'\/', '/').replace(r'\u0026', '&')]
    for source in candidates:
        start = 0
        while True:
            pos = source.find('{"goodsCode":"', start)
            if pos < 0:
                break
            try:
                obj, consumed = decoder.raw_decode(source[pos:])
            except json.JSONDecodeError:
                start = pos + 14
                continue
            code = str(obj.get("goodsCode") or "")
            if code and code not in seen:
                seen.add(code)
                games.append(obj)
            start = pos + max(consumed, 1)
    return games

def normalize_date(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(value or "")[:10]

def normalize_time(value: Any) -> str:
    text = str(value or "")
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 4:
        return f"{digits[:2]}:{digits[2:4]}"
    return text[:5]

def parse_booking_open(value: Any):
    text = str(value or "").strip()

    if not text:
        return None

    digits = re.sub(r"\D", "", text)

    try:
        if len(digits) >= 12:
            return datetime.strptime(
                digits[:12], "%Y%m%d%H%M"
            ).replace(tzinfo=KST)

        if len(digits) >= 8:
            return datetime.strptime(
                digits[:8], "%Y%m%d"
            ).replace(tzinfo=KST)

    except ValueError:
        return None

    return None


def get_nol_booking_status(booking_open: Any, now: datetime) -> str:
    open_datetime = parse_booking_open(booking_open)

    if open_datetime is None:
        return "확인 필요"

    if now >= open_datetime:
        return "예매중"

    return "예매예정"
    

def collect_nol():
    events, status_list = [], []
    for source in CONFIG.get("nol", []):
        status = {"site": "NOL 티켓", "team": source["team"], "success": False, "count": 0, "message": ""}
        try:
            page = fetch(source["pageUrl"], HEADERS_HTML).decode("utf-8", errors="replace")
            raw_games = extract_goods_objects(decode_next_chunks(page))
            if not raw_games:
                raise RuntimeError("페이지에서 경기정보를 찾지 못했습니다.")
            for game in raw_games:
                sport = game.get("sport") or {}
                home = sport.get("homeOrganization") or {}
                away = sport.get("awayOrganization") or {}
                pre_sales = game.get("preSales") or []
                first_pre = pre_sales[0] if pre_sales else {}
                goods_code = str(game.get("goodsCode") or "")
                booking_open = first_pre.get("minBookingOpenTime") or game.get("bookingOpenTime") or ""
                events.append({
                    "id": "NOL-" + goods_code,
                    "site": "NOL 티켓",
                    "sourceTeam": source["team"],
                    "date": normalize_date(sport.get("playDate") or game.get("playStartDate")),
                    "time": normalize_time(sport.get("playTime")),
                    "away": str(away.get("name") or "").strip(),
                    "home": str(home.get("name") or "").strip(),
                    "venue": str(game.get("placeName") or "").strip(),
                    "title": str(game.get("goodsName") or "").strip(),
                    "bookingOpen": str(booking_open),
                    "bookingStatus": "예매정보 확인",
                    "goodsCode": goods_code,
                    "link": source["pageUrl"],
                })
            status["success"] = True
            status["count"] = len(raw_games)
            status["message"] = "페이지 내 경기 JSON 추출 성공"
        except Exception as exc:
            status["message"] = str(exc)
        status_list.append(status)
    return events, status_list

def main():
    now = datetime.now(KST)
    end = now + timedelta(days=int(CONFIG.get("rangeDays", 120)))

    tl_events, tl_status = collect_ticketlink(now, end)
    nol_events, nol_status = collect_nol()

    dedup = {}
    for event in tl_events + nol_events:
        dedup[event["id"]] = event

    events = sorted(
        dedup.values(),
        key=lambda e: (e.get("date", "9999-99-99"), e.get("time", "99:99"), e.get("site", ""))
    )
    payload = {
        "updatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "queryRange": {"startDate": now.strftime("%Y-%m-%d"), "endDate": end.strftime("%Y-%m-%d")},
        "sourceStatus": tl_status + nol_status,
        "events": events,
    }
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    (ROOT / "data.json").write_text(json_text + "\n", encoding="utf-8")
    (ROOT / "data.js").write_text("window.SPORTS_DATA = " + json_text + ";\n", encoding="utf-8")
    print(f"총 {len(events)}건 생성: 티켓링크 {len(tl_events)}건, NOL {len(nol_events)}건")

if __name__ == "__main__":
    main()
