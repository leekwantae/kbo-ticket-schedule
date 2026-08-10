import json, os, re, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path("kbo_live.json")
BOT = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]
KST = ZoneInfo("Asia/Seoul")

with DATA.open("r", encoding="utf-8-sig") as f:
    payload = json.load(f)

now = datetime.now(KST)
today_key = now.strftime("%Y.%m.%d")
title = now.strftime("%Y년 %m월 %d일")
rx = re.compile(r"(\d{4}\.\d{2}\.\d{2}).*?(\d{2}:\d{2})")

rows = []
for g in payload.get("events", []):
    m = rx.search(str(g.get("openDate") or ""))
    if not m or m.group(1) != today_key:
        continue
    rows.append({
        "open": m.group(2),
        "home": g.get("homeTeam") or "-",
        "away": g.get("awayTeam") or "-",
        "place": g.get("place") or "-",
        "date": g.get("date") or "-",
        "time": g.get("time") or "-",
        "site": g.get("platformName") or g.get("platform") or "-",
        "url": g.get("pageUrl") or ""
    })

rows.sort(key=lambda x: (x["open"], x["date"], x["time"], x["home"]))

lines = ["🔥 오늘의 KBO 예매 일정", title, ""]
if not rows:
    lines.append("오늘 오픈 예정인 경기가 없습니다.")
else:
    for i, g in enumerate(rows, 1):
        lines += [
            f"🎫 {g['open']} 오픈",
            f"{g['home']} vs {g['away']}",
            f"🏟 {g['place']}",
            f"⚾ 경기: {g['date']} {g['time']}",
            f"🔗 예매처: {g['site']}",
        ]
        if g["url"]:
            lines.append(g["url"])
        if i != len(rows):
            lines.append("")
    lines += ["", f"오늘 예매 예정: {len(rows)}경기"]

updated = payload.get("updatedAtLocal") or payload.get("updatedAt")
if updated:
    lines.append(f"데이터 갱신: {updated}")

msg = "\n".join(lines)
url = f"https://api.telegram.org/bot{BOT}/sendMessage"
data = urllib.parse.urlencode({
    "chat_id": CHAT,
    "text": msg,
    "disable_web_page_preview": "true"
}).encode()

req = urllib.request.Request(url, data=data, method="POST")
with urllib.request.urlopen(req, timeout=20) as r:
    res = json.loads(r.read().decode())
if not res.get("ok"):
    raise SystemExit(res)
print(f"전송 완료: {len(rows)}경기")
