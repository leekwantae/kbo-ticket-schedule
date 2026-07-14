# KBO 경기·예매 일정

## 업로드
압축을 푼 뒤 폴더 안의 모든 파일과 `.github` 폴더를 GitHub 저장소 최상위에 업로드합니다.

## 최초 실행
1. 저장소의 Actions 탭으로 이동
2. `KBO 경기정보 갱신 및 Pages 배포` 선택
3. `Run workflow` 실행
4. Settings → Pages → Source를 `GitHub Actions`로 설정

## 데이터 방식
- 티켓링크: `mapi.ticketlink.co.kr/mapi/sports/schedules` JSON API
- NOL 티켓 두산·키움: 공개 페이지에 포함된 경기 JSON 추출
- 4시간마다 자동 갱신
