import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


SOURCE_URL = "https://mainichi.jp/hiroshima/"
OUTPUT_FILE = "mainichi_hiroshima.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://mainichi.jp/",
}


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def clean_title(text):
    text = clean_text(text)

    # タイトルの後ろに続く
    # 「2026/10/14 05:02 2268文字 本文...」
    # の部分を切り落とす
    text = re.split(
        r"\s+20\d{2}/\d{1,2}/\d{1,2}"
        r"\s+\d{1,2}:\d{2}",
        text,
        maxsplit=1,
    )[0]

    return clean_text(text)


def canonicalize_url(url):
    parsed = urlparse(url)

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"{parsed.path}"
    )


def make_guid(link):
    return hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()


def fetch_page(url):
    for attempt in range(1, 4):
        try:
            print(
                f"取得 {attempt}/3: {url}"
            )

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )

            print(
                f"HTTP: {response.status_code}"
            )

            response.raise_for_status()

            response.encoding = (
                response.apparent_encoding
                or "utf-8"
            )

            return response.text

        except requests.RequestException as e:
            print(
                f"取得失敗: {e}"
            )

            if attempt < 3:
                print(
                    "10秒待って再試行します。"
                )
                time.sleep(10)

    return None


def parse_datetime_string(text):
    if not text:
        return None

    text = clean_text(text)

    patterns = [
        (
            r"(20\d{2})/"
            r"(\d{1,2})/"
            r"(\d{1,2})"
            r"\s+"
            r"(\d{1,2}):"
            r"(\d{2})",
            True,
        ),
        (
            r"(20\d{2})年"
            r"(\d{1,2})月"
            r"(\d{1,2})日"
            r"\s*"
            r"(\d{1,2}):"
            r"(\d{2})",
            True,
        ),
        (
            r"(20\d{2})/"
            r"(\d{1,2})/"
            r"(\d{1,2})",
            False,
        ),
        (
            r"(20\d{2})年"
            r"(\d{1,2})月"
            r"(\d{1,2})日",
            False,
        ),
    ]

    for pattern, has_time in patterns:
        match = re.search(
            pattern,
            text,
        )

        if not match:
            continue

        try:
            if has_time:
                return datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    int(match.group(4)),
                    int(match.group(5)),
                    0,
                    tzinfo=JST,
                )

            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                12,
                0,
                0,
                tzinfo=JST,
            )

        except ValueError:
            continue

    return None


def parse_datetime_attribute(value):
    if not value:
        return None

    value = value.strip()

    try:
        normalized = value.replace(
            "Z",
            "+00:00",
        )

        dt = datetime.fromisoformat(
            normalized
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=JST
            )

        return dt.astimezone(JST)

    except ValueError:
        return parse_datetime_string(
            value
        )


def load_existing_items():
    items = {}

    if not os.path.exists(
        OUTPUT_FILE
    ):
        return items

    try:
        tree = ET.parse(
            OUTPUT_FILE
        )

        root = tree.getroot()

        for item in root.findall(
            "./channel/item"
        ):
            link = (
                item.findtext(
                    "link",
                    "",
                )
                .strip()
            )

            guid = (
                item.findtext(
                    "guid",
                    "",
                )
                .strip()
            )

            if not guid and link:
                guid = make_guid(
                    link
                )

            if not guid:
                continue

            items[guid] = {
                "title": item.findtext(
                    "title",
                    "",
                ),
                "link": link,
                "description": item.findtext(
                    "description",
                    "",
                ),
                "pubDate": item.findtext(
                    "pubDate",
                    "",
                ),
                "guid": guid,
            }

    except Exception as e:
        print(
            f"既存RSS読込失敗: {e}"
        )

    return items


def is_article_url(url):
    parsed = urlparse(url)

    if parsed.netloc not in (
        "mainichi.jp",
        "www.mainichi.jp",
    ):
        return False

    path = parsed.path

    if not path:
        return False

    # 通常記事
    if re.search(
        r"/articles/20\d{6}/",
        path,
    ):
        return True

    # 大学記事など
    if re.search(
        r"/[^/]+/articles/20\d{6}/",
        path,
    ):
        return True

    return False


def find_container(element):
    current = element

    for _ in range(8):
        current = current.parent

        if current is None:
            break

        text = clean_text(
            current.get_text(
                " ",
                strip=True,
            )
        )

        if len(text) > 2000:
            break

        has_date = (
            parse_datetime_string(
                text
            )
            is not None
        )

        if has_date:
            return current

    return element.parent


def find_datetime(container):
    if container:
        for time_element in container.find_all(
            "time"
        ):
            dt = parse_datetime_attribute(
                time_element.get(
                    "datetime",
                    "",
                )
            )

            if dt:
                return dt

            dt = parse_datetime_string(
                time_element.get_text(
                    " ",
                    strip=True,
                )
            )

            if dt:
                return dt

    if container:
        for element in container.find_all(
            True
        ):
            for attr_name in (
                "datetime",
                "data-date",
                "data-time",
                "data-datetime",
                "data-published",
            ):
                value = element.get(
                    attr_name
                )

                if not value:
                    continue

                dt = parse_datetime_attribute(
                    value
                )

                if dt:
                    return dt

    if container:
        return parse_datetime_string(
            container.get_text(
                " ",
                strip=True,
            )
        )

    return None


def extract_description(
    container,
    title,
):
    if not container:
        return "毎日新聞 広島"

    text = clean_text(
        container.get_text(
            " ",
            strip=True,
        )
    )

    # タイトルより後ろの日時・文字数を起点に
    # 本文概要部分を取得
    match = re.search(
        r"20\d{2}/\d{1,2}/\d{1,2}"
        r"\s+\d{1,2}:\d{2}"
        r"\s+(?:\d+文字)?\s*(.*)",
        text,
    )

    if match:
        description = clean_text(
            match.group(1)
        )

        if description:
            if len(description) > 500:
                description = (
                    description[:500]
                    + "…"
                )

            return description

    return "毎日新聞 広島"


def extract_articles(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    articles = {}

    for a in soup.find_all(
        "a",
        href=True,
    ):
        raw_text = clean_text(
            a.get_text(
                " ",
                strip=True,
            )
        )

        if not raw_text:
            continue

        link = urljoin(
            SOURCE_URL,
            a["href"],
        )

        link = canonicalize_url(
            link
        )

        if not is_article_url(
            link
        ):
            continue

        title = clean_title(
            raw_text
        )

        if len(title) < 8:
            continue

        container = find_container(
            a
        )

        published = find_datetime(
            container
        )

        if published is None:
            continue

        description = extract_description(
            container,
            title,
        )

        candidate = {
            "title": title,
            "link": link,
            "date": published,
            "description": description,
        }

        existing = articles.get(
            link
        )

        if existing is None:
            articles[link] = candidate

        else:
            # 同じ記事URLが複数箇所にある場合は、
            # 日時などを除去した後で
            # より情報量のあるタイトルを採用
            if (
                len(title)
                > len(existing["title"])
            ):
                articles[link] = candidate

    result = list(
        articles.values()
    )

    result.sort(
        key=lambda x: (
            x["date"],
            x["link"],
        ),
        reverse=True,
    )

    return result


def build_rss(
    existing_items,
    current_articles,
):
    merged = dict(
        existing_items
    )

    new_count = 0

    for article in current_articles:
        guid = make_guid(
            article["link"]
        )

        if guid not in merged:
            new_count += 1

        merged[guid] = {
            "title": article["title"],
            "link": article["link"],
            "description": article[
                "description"
            ],
            "pubDate": format_datetime(
                article["date"]
            ),
            "guid": guid,
        }

    def get_pubdate(item):
        try:
            dt = parsedate_to_datetime(
                item["pubDate"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            return dt

        except Exception:
            return datetime(
                1970,
                1,
                1,
                tzinfo=timezone.utc,
            )

    sorted_items = sorted(
        merged.values(),
        key=get_pubdate,
        reverse=True,
    )

    rss = ET.Element(
        "rss",
        version="2.0",
    )

    channel = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        channel,
        "title",
    ).text = "毎日新聞 広島"

    ET.SubElement(
        channel,
        "link",
    ).text = SOURCE_URL

    ET.SubElement(
        channel,
        "description",
    ).text = (
        "毎日新聞「広島」の新着記事"
    )

    ET.SubElement(
        channel,
        "language",
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        datetime.now(JST)
    )

    for data in sorted_items:
        item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = data["title"]

        ET.SubElement(
            item,
            "link",
        ).text = data["link"]

        ET.SubElement(
            item,
            "description",
        ).text = data[
            "description"
        ]

        ET.SubElement(
            item,
            "pubDate",
        ).text = data[
            "pubDate"
        ]

        guid_element = ET.SubElement(
            item,
            "guid",
        )

        guid_element.set(
            "isPermaLink",
            "false",
        )

        guid_element.text = data[
            "guid"
        ]

    tree = ET.ElementTree(
        rss
    )

    ET.indent(
        tree,
        space="  ",
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    return (
        len(sorted_items),
        new_count,
    )


def main():
    existing_items = (
        load_existing_items()
    )

    print(
        f"既存RSS件数: "
        f"{len(existing_items)}"
    )

    html = fetch_page(
        SOURCE_URL
    )

    if html is None:
        if existing_items:
            print(
                "取得に失敗したため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            "毎日新聞 広島ページの"
            "取得に失敗しました。"
        )

    print(
        f"HTML文字数: {len(html)}"
    )

    articles = extract_articles(
        html
    )

    print()
    print(
        f"記事取得件数: "
        f"{len(articles)}"
    )

    print()
    print("=== 取得記事 ===")

    for i, article in enumerate(
        articles,
        1,
    ):
        print()
        print(
            f"{i:2d}. "
            f"{article['date'].strftime('%Y-%m-%d %H:%M')} "
            f"{article['title']}"
        )

        print(
            f"    {article['link']}"
        )

    if len(articles) < 3:
        if existing_items:
            print()
            print(
                "記事取得件数が異常に少ないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            f"記事取得件数が少なすぎます: "
            f"{len(articles)}"
        )

    total_count, new_count = (
        build_rss(
            existing_items,
            articles,
        )
    )

    print()
    print(
        f"今回の新規記事: "
        f"{new_count}"
    )

    print(
        f"RSS保存件数: "
        f"{total_count}"
    )

    print(
        f"保存先: "
        f"{os.path.abspath(OUTPUT_FILE)}"
    )


if __name__ == "__main__":
    main()