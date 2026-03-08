"""자동차 뉴스 & 유튜브 트렌드 리포트 — Slack 발송."""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from html import unescape
from pathlib import Path

# ── 경로 / 환경변수 ─────────────────────────────────────────────────────
ROOT = Path(__file__).parent
STATE_PATH = ROOT / "states" / "tracker_state.json"

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "C0AK9JXU6N8")

KST = timezone(timedelta(hours=9))


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────
def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    STATE_PATH.parent.mkdir(exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def fetch_rss(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  RSS 오류: {e} - {url}")
        return ""


def parse_rss_items(rss_text, max_items=15):
    """RSS/Atom 피드에서 항목 파싱"""
    articles = []

    # RSS <item>
    items = re.findall(r"<item>(.*?)</item>", rss_text, re.DOTALL)
    for item in items[:max_items]:
        title_m = re.search(r"<title[^>]*>(.*?)</title>", item, re.DOTALL)
        link_m = re.search(r"<link[^>]*>(https?://[^<\s]+)</link>", item)
        if not link_m:
            link_m = re.search(r"<link[^>]*href=[\"']([^\"']+)[\"']", item)
        if not title_m or not link_m:
            continue
        title = unescape(re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title_m.group(1))).strip()
        url = unescape(link_m.group(1)).strip()
        if title and len(title) >= 5:
            articles.append({"title": title, "url": url})

    # Atom <entry>
    if not articles:
        entries = re.findall(r"<entry>(.*?)</entry>", rss_text, re.DOTALL)
        for entry in entries[:max_items]:
            title_m = re.search(r"<title[^>]*>(.*?)</title>", entry, re.DOTALL)
            link_m = re.search(r"<link[^>]*href=[\"']([^\"']+)[\"']", entry)
            if not title_m or not link_m:
                continue
            title = unescape(re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title_m.group(1))).strip()
            url = unescape(link_m.group(1)).strip()
            if title and len(title) >= 5:
                articles.append({"title": title, "url": url})

    return articles


def slack_post(blocks, thread_ts=None):
    payload = {
        "channel": SLACK_CHANNEL,
        "blocks": blocks,
        "unfurl_links": False,
        "unfurl_media": False,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
            if not body.get("ok"):
                print(f"  Slack API 오류: {body.get('error')}")
                return None
            return body.get("ts")
    except urllib.error.HTTPError as e:
        print(f"  Slack 오류: {e.code} {e.reason}")
        return None


def lines_to_blocks(lines):
    blocks = []
    chunk = []
    chunk_len = 0
    for line in lines:
        if chunk and chunk_len + len(line) + 1 > 800:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(chunk)}})
            chunk = []
            chunk_len = 0
        chunk.append(line)
        chunk_len += len(line) + 1
    if chunk:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(chunk)}})
    return blocks


# ── 뉴스 소스 정의 ───────────────────────────────────────────────────────
# 국내 자동차 뉴스
KR_NEWS_SOURCES = [
    ("모터그래프", "https://www.motorgraph.com/rss/allArticle.xml"),
    ("오토데일리", "https://www.autodaily.co.kr/rss/allArticle.xml"),
    ("카가이", "https://www.carguy.kr/rss/allArticle.xml"),
    ("오토모빌코리아", "https://www.autopostkorea.com/feed/"),
]

# 글로벌 자동차 뉴스
GLOBAL_NEWS_SOURCES = [
    ("CarScoops", "https://www.carscoops.com/feed/"),
]

# 자동차 키워드 (오토데일리 등 종합 매체에서 자동차 기사 필터링용)
CAR_KEYWORDS = [
    "차", "자동차", "suv", "세단", "전기차", "하이브리드", "ev",
    "신차", "출시", "모델", "엔진", "배터리", "충전", "주행",
    "현대", "기아", "제네시스", "bmw", "벤츠", "아우디", "테슬라",
    "도요타", "혼다", "폭스바겐", "포르쉐", "람보르기니", "페라리",
    "쉐보레", "포드", "르노", "쌍용", "KG모빌리티",
    "car", "vehicle", "motor", "drive", "auto",
    "튜닝", "리콜", "연비", "마력", "토크",
]

# 유튜브 채널 (자동차)
YOUTUBE_CHANNELS = [
    ("모트라인", "UCj_cl6JoW02GZEXFhRqmlvA"),
    ("모터그래프", "UCwFikRfzWHBhkESS6PpWH0g"),
    ("모터피디", "UCwJgdjQ159zJ5rPhQcOU9GQ"),
    ("Doug DeMuro", "UCG72WbiCvdB6JKU-3YRP8Kg"),
    ("Top Gear", "UCx6xmIOXqnQ79ZI6VVpARXw"),
]


# ── 수집 함수 ─────────────────────────────────────────────────────────────
def matches_car_keywords(title):
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in CAR_KEYWORDS)


def collect_kr_news():
    """국내 자동차 뉴스 수집"""
    articles = []
    for name, url in KR_NEWS_SOURCES:
        rss = fetch_rss(url)
        if not rss:
            continue
        items = parse_rss_items(rss, max_items=15)
        # 자동차 키워드 필터링 (전문 매체는 대부분 해당)
        filtered = []
        for item in items:
            if matches_car_keywords(item["title"]):
                item["source"] = name
                filtered.append(item)
        articles.extend(filtered)
        print(f"  {name}: {len(items)}건 중 {len(filtered)}건 자동차 관련")
    return articles


def collect_global_news():
    """글로벌 자동차 뉴스 수집"""
    articles = []
    for name, url in GLOBAL_NEWS_SOURCES:
        rss = fetch_rss(url)
        if not rss:
            continue
        items = parse_rss_items(rss, max_items=10)
        for item in items:
            item["source"] = name
        articles.extend(items)
        print(f"  {name}: {len(items)}건")
    return articles


def collect_youtube():
    """유튜브 자동차 채널 최신 영상 수집"""
    videos = []
    for name, channel_id in YOUTUBE_CHANNELS:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        rss = fetch_rss(url)
        if not rss:
            continue
        entries = re.findall(r"<entry>(.*?)</entry>", rss, re.DOTALL)
        count = 0
        for entry in entries[:5]:
            title_m = re.search(r"<title>(.*?)</title>", entry)
            vid_m = re.search(r"<yt:videoId>(.*?)</yt:videoId>", entry)
            if not title_m or not vid_m:
                continue
            title = unescape(title_m.group(1)).strip()
            video_id = vid_m.group(1)
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            videos.append({
                "title": title,
                "url": video_url,
                "source": name,
            })
            count += 1
        print(f"  {name}: {count}건")
    return videos


# ── 메시지 빌드 ───────────────────────────────────────────────────────────
def build_header():
    _w = ["월", "화", "수", "목", "금", "토", "일"]
    _t = datetime.now(KST)
    _d = f"{_t.strftime('%m-%d')}({_w[_t.weekday()]})"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"자동차 뉴스 & 트렌드 리포트 | {_d}"}},
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "신차 소식, 자동차 뉴스, 유튜브 트렌드를 매일 정리합니다.\n"
                    "상세 내역은 스레드를 확인해주세요."
                ),
            },
        },
    ]
    return blocks


def build_article_thread(title, articles, seen_urls):
    """아티클 목록을 스레드 블록으로 변환 (중복 제거)"""
    new_articles = [a for a in articles if a["url"] not in seen_urls]

    unique = {}
    for a in new_articles:
        if a["url"] not in unique:
            unique[a["url"]] = a
    new_articles = list(unique.values())

    lines = [f"*{title}*\n"]

    if not new_articles:
        lines.append("새로운 소식 없음")
        return lines_to_blocks(lines), []

    for i, a in enumerate(new_articles[:10], 1):
        source = a.get("source", "")
        lines.append(f"{i}. <{a['url']}|{a['title']}> ({source})")

    return lines_to_blocks(lines), [a["url"] for a in new_articles[:10]]


# ── 메인 ─────────────────────────────────────────────────────────────────
def main():
    now_kst = datetime.now(KST)

    if not SLACK_BOT_TOKEN:
        print("SLACK_BOT_TOKEN 미설정.")
        sys.exit(1)

    print(f"자동차 뉴스 수집 시작 ({now_kst.strftime('%Y-%m-%d %H:%M')} KST)...")

    # 데이터 수집
    print("\n[국내 자동차 뉴스]")
    kr_news = collect_kr_news()

    print("\n[글로벌 자동차 뉴스]")
    global_news = collect_global_news()

    print("\n[유튜브 자동차 채널]")
    youtube_videos = collect_youtube()

    print(f"\n수집 결과: 국내 {len(kr_news)}건, 글로벌 {len(global_news)}건, "
          f"유튜브 {len(youtube_videos)}건")

    # 이전 상태 로드
    prev_state = load_state()
    seen_urls = set(prev_state.get("seen_urls", []))

    # 1) 메인 메시지
    header_blocks = build_header()
    ts = slack_post(header_blocks)
    if not ts:
        print("메인 메시지 전송 실패. 종료.")
        return
    print(f"\n메인 메시지 전송 (ts={ts})")

    all_new_urls = []

    # 2) 국내 자동차 뉴스
    blocks, new_urls = build_article_thread(
        "[국내 자동차 뉴스]", kr_news, seen_urls
    )
    slack_post(blocks, thread_ts=ts)
    all_new_urls.extend(new_urls)
    print(f"  스레드: 국내 뉴스 ({len(new_urls)}건)")

    # 3) 글로벌 자동차 뉴스
    blocks, new_urls = build_article_thread(
        "[글로벌 자동차 뉴스]", global_news, seen_urls
    )
    slack_post([{"type": "divider"}] + blocks, thread_ts=ts)
    all_new_urls.extend(new_urls)
    print(f"  스레드: 글로벌 뉴스 ({len(new_urls)}건)")

    # 4) 유튜브 자동차 트렌드
    blocks, new_urls = build_article_thread(
        "[유튜브 자동차 트렌드]", youtube_videos, seen_urls
    )
    slack_post([{"type": "divider"}] + blocks, thread_ts=ts)
    all_new_urls.extend(new_urls)
    print(f"  스레드: 유튜브 ({len(new_urls)}건)")

    # 상태 저장
    all_seen = list(seen_urls | set(all_new_urls))
    new_state = {
        "updated": now_kst.isoformat(),
        "seen_urls": all_seen[-300:],
    }
    save_state(new_state)
    print("\n상태 저장 완료")
    print("완료.")


if __name__ == "__main__":
    main()
