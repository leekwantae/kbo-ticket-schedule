from __future__ import annotations

import html
import json
import re
import time
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

SPECIAL_EVENT_KEYWORDS = (
    "팬투어",
    "팬미팅",
    "팬페스티벌",
    "페스티벌",
    "이벤트",
    "투어",
    "사인회",
    "체험",
)


def fetch(url: str, headers: dict[str, str], retries: int = 3) -> bytes:
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=40) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))

    assert last_error is not None
    raise last_error


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
        return str(
            value.get("teamName")
            or value.get("teamShortName")
            or value.get("name")
            or ""
        ).strip()
    return ""


def parse_booking_open(value: Any):
    if value in (None, "", 0):
        return None

    if isinstance(value, datetime):
        return value.astimezone(KST) if value.tzinfo else value.replace(tzinfo=KST)

    digits = re.sub(r"\D", "", str(value).strip())

    try:
        if len(digits) >= 14:
            return datetime.strptime(digits[:14], "%Y%m%d%H%M%S").replace(tzinfo=KST)
        if len(digits) >= 12:
            return datetime.strptime(digits[:12], "%Y%m%d%H%M").replace(tzinfo=KST)
        if len(digits) >= 8:
            return datetime.strptime(digits[:8], "%Y%m%d").replace(tzinfo=KST)
    except ValueError:
        return None

    return None


def get_booking_status(
    booking_open: Any,
    now: datetime,
    status_code: str = "",
) -> str:
    code = str(status_code or "").upper()

    if code in {"ON_SALE", "SALE", "BOOKING", "AVAILABLE"}:
        return "예매중"
    if code in {"SOLD_OUT", "SOLDOUT"}:
        return "매진"
    if code in {"CLOSED", "SALE_END", "ENDED"}:
        return "예매종료"
    if code in {"CANCEL", "CANCELED", "CANCELLED"}:
        return "취소"

    open_datetime = parse_booking_open(booking_open)
    if open_datetime is None:
        return "확인 필요"

    return "예매중" if now >= open_datetime else "예매예정"


def is_special_event(title: str, away: str, home: str) -> bool:
    """
    양 팀이 모두 있으면 프로모션 명칭이 있어도 일반 경기로 처리한다.
    예: '2026 KT 워터페스티벌'은 키움 vs KT 경기의 이벤트명이지 별도 행사가 아님.
    """
    if away and home:
        return False

    clean_title = str(title or "").strip()
    return bool(
        clean_title
        and any(keyword in clean_title for keyword in SPECIAL_EVENT_KEYWORDS)
    )


def load_previous_payload() -> dict[str, Any]:
    """기존 data.json을 읽어 API가 일시적으로 0건을 반환할 때 보존용으로 사용합니다."""
    path = ROOT / "data.json"
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def previous_ticketlink_events(
    previous_payload: dict[str, Any],
    source_team: str,
) -> list[dict[str, Any]]:
    events = previous_payload.get("events", [])
    if not isinstance(events, list):
        return []

    return [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("site") == "티켓링크"
        and event.get("sourceTeam") == source_team
    ]


def extract_schedules(payload: Any) -> list[dict[str, Any]]:
    """티켓링크 응답 구조가 조금 달라져도 schedules 배열을 찾습니다."""
    candidates: list[Any] = []

    if isinstance(payload, dict):
        candidates.append(payload.get("schedules"))

        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend([
                data.get("schedules"),
                data.get("scheduleList"),
                data.get("items"),
                data.get("list"),
            ])
        elif isinstance(data, list):
            candidates.append(data)

        result = payload.get("result")
        if isinstance(result, dict):
            candidates.extend([
                result.get("schedules"),
                result.get("scheduleList"),
                result.get("items"),
                result.get("list"),
            ])

    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]

    return []


def collect_ticketlink(now: datetime, end: datetime, previous_payload: dict[str, Any]):
    events, status_list = [], []

    for source in CONFIG.get("ticketlink", []):
        params = urllib.parse.urlencode(
            {
                "categoryId": source["categoryId"],
                "teamId": source["teamId"],
                "startDate": now.strftime("%Y%m%d"),
                "endDate": end.strftime("%Y%m%d"),
            }
        )
        url = f"{TICKETLINK_API}?{params}"

        status = {
            "site": "티켓링크",
            "team": source["team"],
            "success": False,
            "count": 0,
            "message": "",
        }

        try:
            payload = fetch_json(url)
            schedules = extract_schedules(payload)

            # API가 오류 없이 빈 배열을 주는 경우가 있어, 기존 정상 데이터를 보존합니다.
            if not schedules:
                previous = previous_ticketlink_events(
                    previous_payload,
                    source["team"],
                )
                if previous:
                    events.extend(previous)
                    status["success"] = True
                    status["count"] = len(previous)
                    status["message"] = "API 0건 · 이전 정상 데이터 유지"
                    status_list.append(status)
                    continue

                raise RuntimeError("티켓링크 API가 일정 0건을 반환했습니다.")

            source_event_count = 0

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
                title = str(
                    item.get("matchTitle")
                    or item.get("productName")
                    or ""
                ).strip()
                away = team_name(item.get("awayTeam"))
                home = team_name(item.get("homeTeam"))
                special = is_special_event(title, away, home)

                events.append(
                    {
                        "id": "TL-" + (
                            schedule_id
                            or f"{source['team']}-{game.isoformat()}-{title}"
                        ),
                        "site": "티켓링크",
                        "sourceTeam": source["team"],
                        "date": game.strftime("%Y-%m-%d"),
                        "time": game.strftime("%H:%M"),
                        "away": away,
                        "home": home,
                        "venue": str(item.get("venueName") or "").strip(),
                        "title": title,
                        "eventType": "행사" if special else "경기",
                        "displayName": title if special else "",
                        "bookingOpen": (
                            reserve.strftime("%Y-%m-%d %H:%M") if reserve else ""
                        ),
                        "bookingStatus": get_booking_status(
                            reserve, now, status_code
                        ),
                        "scheduleId": schedule_id,
                        "productId": str(item.get("productId") or ""),
                        "link": source["pageUrl"],
                    }
                )

                source_event_count += 1

            if source_event_count == 0:
                previous = previous_ticketlink_events(
                    previous_payload,
                    source["team"],
                )
                if previous:
                    events.extend(previous)
                    status["success"] = True
                    status["count"] = len(previous)
                    status["message"] = "일정 해석 0건 · 이전 정상 데이터 유지"
                    status_list.append(status)
                    continue
                raise RuntimeError("일정 배열은 있으나 유효한 경기를 찾지 못했습니다.")

            status["success"] = True
            status["count"] = source_event_count
            status["message"] = "API 조회 성공"

        except Exception as exc:
            previous = previous_ticketlink_events(
                previous_payload,
                source["team"],
            )
            if previous:
                events.extend(previous)
                status["success"] = True
                status["count"] = len(previous)
                status["message"] = f"수집 실패 · 이전 정상 데이터 유지: {exc}"
            else:
                status["message"] = str(exc)

        status_list.append(status)

    return events, status_list


def decode_next_chunks(page: str) -> str:
    chunks = []
    pattern = re.compile(
        r'self\.__next_f\.push\(\[1,\s*("(?:\\.|[^"\\])*")\]\)',
        re.DOTALL,
    )

    for match in pattern.finditer(page):
        try:
            chunks.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass

    return page + "\n" + "\n".join(chunks)


def extract_goods_objects(text: str) -> list[dict]:
    text = html.unescape(text)
    decoder, games, seen = json.JSONDecoder(), [], set()

    candidates = [
        text,
        text.replace(r'\"', '"').replace(r"\/", "/").replace(r"\u0026", "&"),
    ]

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
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 4:
        return f"{digits[:2]}:{digits[2:4]}"
    return str(value or "")[:5]


def collect_nol(now: datetime):
    events, status_list = [], []

    for source in CONFIG.get("nol", []):
        status = {
            "site": "NOL 티켓",
            "team": source["team"],
            "success": False,
            "count": 0,
            "message": "",
        }

        try:
            page = fetch(source["pageUrl"], HEADERS_HTML).decode(
                "utf-8",
                errors="replace",
            )
            raw_games = extract_goods_objects(decode_next_chunks(page))

            if not raw_games:
                raise RuntimeError("페이지에서 경기정보를 찾지 못했습니다.")

            for game in raw_games:
                sport = game.get("sport") or {}
                home = sport.get("homeOrganization") or {}
                away = sport.get("awayOrganization") or {}

                pre_sales = game.get("preSales") or []

                    # NOL에 등록된 모든 예매 시작 시간을 모읍니다.
                    booking_candidates = []
                    
                    for pre_sale in pre_sales:
                        if not isinstance(pre_sale, dict):
                            continue
                    
                        value = (
                            pre_sale.get("minBookingOpenTime")
                            or pre_sale.get("bookingOpenTime")
                            or pre_sale.get("openDateTime")
                        )
                    
                        if value:
                            booking_candidates.append(value)
                    
                    # 상품 자체에 일반예매 시간이 있으면 함께 비교합니다.
                    game_booking_open = game.get("bookingOpenTime")
                    if game_booking_open:
                        booking_candidates.append(game_booking_open)
                    
                    # 선예매보다 일반예매가 늦게 시작하므로 가장 늦은 시간을 선택합니다.
                    parsed_candidates = []
                    
                    for value in booking_candidates:
                        parsed = parse_booking_open(value)
                        if parsed is not None:
                            parsed_candidates.append((parsed, value))
                    
                    if parsed_candidates:
                        parsed_candidates.sort(key=lambda item: item[0])
                        booking_open = parsed_candidates[-1][1]
                    else:
                        booking_open = game_booking_open or ""
    
                title = str(game.get("goodsName") or "").strip()
                away_name = str(away.get("name") or "").strip()
                home_name = str(home.get("name") or "").strip()
                special = is_special_event(title, away_name, home_name)

                events.append(
                    {
                        "id": "NOL-" + goods_code,
                        "site": "NOL 티켓",
                        "sourceTeam": source["team"],
                        "date": normalize_date(
                            sport.get("playDate") or game.get("playStartDate")
                        ),
                        "time": normalize_time(sport.get("playTime")),
                        "away": away_name,
                        "home": home_name,
                        "venue": str(game.get("placeName") or "").strip(),
                        "title": title,
                        "eventType": "행사" if special else "경기",
                        "displayName": title if special else "",
                        "bookingOpen": str(booking_open),
                        "bookingStatus": get_booking_status(booking_open, now),
                        "goodsCode": goods_code,
                        "link": source["pageUrl"],
                    }
                )

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

    previous_payload = load_previous_payload()

    tl_events, tl_status = collect_ticketlink(
        now,
        end,
        previous_payload,
    )
    nol_events, nol_status = collect_nol(now)

    # scheduleId와 goodsCode는 각각 고유하므로 실제 동일 ID만 제거한다.
    # 행사명과 경기명이 겹친다는 이유로 서로 다른 경기를 지우지 않는다.
    dedup: dict[str, dict[str, Any]] = {}
    for event in tl_events + nol_events:
        dedup[event["id"]] = event

    events = sorted(
        dedup.values(),
        key=lambda e: (
            e.get("date", "9999-99-99"),
            e.get("time", "99:99"),
            e.get("site", ""),
        ),
    )

    previous_tl = [
        event
        for event in previous_payload.get("events", [])
        if isinstance(event, dict) and event.get("site") == "티켓링크"
    ]

    if not tl_events and previous_tl:
        raise RuntimeError(
            "티켓링크 데이터가 0건이어서 기존 data.json/data.js를 보호했습니다."
        )

    payload = {
        "updatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "queryRange": {
            "startDate": now.strftime("%Y-%m-%d"),
            "endDate": end.strftime("%Y-%m-%d"),
        },
        "sourceStatus": tl_status + nol_status,
        "events": events,
    }

    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    (ROOT / "data.json").write_text(json_text + "\n", encoding="utf-8")
    (ROOT / "data.js").write_text(
        "window.SPORTS_DATA = " + json_text + ";\n",
        encoding="utf-8",
    )

    print(
        f"총 {len(events)}건 생성: "
        f"티켓링크 {len(tl_events)}건, "
        f"NOL {len(nol_events)}건"
    )


if __name__ == "__main__":
    main()
