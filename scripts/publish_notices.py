"""운영팀 게시글을 읽어 게임용 피드를 생성합니다. 게시글 내용을 코드로 실행하지 않습니다."""
from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from notice_photos import publish_photo

MAX_BYTES = 256 * 1024
MAX_NOTICES = 50
STAFF = {"OWNER", "MEMBER", "COLLABORATOR"}
MARKDOWN_IMAGE = re.compile(r'!\[[^\]]*\]\(\s*(https://[^\s)]+)(?:\s+["\'][^"\']*["\'])?\s*\)')


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            source = dict(attrs).get("src")
            if source:
                self.images.append(source)
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def section(body: str, label: str) -> str:
    match = re.search(r"^### " + re.escape(label) + r"\s*$\n(.*?)(?=^### |\Z)", body, re.M | re.S)
    return match.group(1).strip() if match else ""


def image_url(value: str, repository: str) -> str:
    value = html.unescape(value)
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or len(value) > 2048:
        raise ValueError("사진은 GitHub에 첨부한 공개 HTTPS 이미지를 사용해주세요.")
    host = parsed.hostname or ""
    allowed = host == "user-images.githubusercontent.com" or (
        host == "github.com" and parsed.path.startswith("/user-attachments/assets/")
    ) or (host == "raw.githubusercontent.com" and parsed.path.startswith("/" + repository + "/main/images/"))
    if not allowed:
        raise ValueError("외부 사진 주소 대신 본문 입력창에 사진을 첨부해주세요.")
    return value


def convert(issue: dict, repository: str) -> dict | None:
    if issue.get("pull_request") or issue.get("state") != "open" or issue.get("author_association") not in STAFF:
        return None
    if "notice" not in {label["name"] for label in issue.get("labels", [])}:
        return None
    form = issue.get("body") or ""
    if section(form, "게시 상태") != "게시":
        return None
    content = section(form, "본문")
    if not content or content == "_No response_":
        raise ValueError("본문을 입력해주세요.")
    urls: list[str] = []

    def replace_image(match: re.Match) -> str:
        urls.append(image_url(match.group(1), repository))
        return "\n"

    content = MARKDOWN_IMAGE.sub(replace_image, content)
    parser = PlainText()
    parser.feed(content)
    parser.close()
    urls.extend(image_url(url, repository) for url in parser.images)
    urls = list(dict.fromkeys(urls))
    if len(urls) > 5:
        raise ValueError("사진은 최대 5장까지 첨부해주세요.")
    text = "".join(parser.parts)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\((https://[^\s)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    title = (issue.get("title") or "").strip()
    if not title or len(title) > 128 or not text or len(text) > 8000:
        raise ValueError("제목은 1~128자, 본문은 1~8,000자로 작성해주세요.")
    created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Seoul"))
    return {"id": f"issue-{int(issue['number'])}", "date": created.strftime("%Y-%m-%d"),
            "title": title, "body": text, "imageUrls": urls}


def request_json(path: str) -> list | dict:
    request = urllib.request.Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "WhereWhale-NoticePublisher"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> int:
    repository = os.environ["GH_REPO"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("저장소 이름을 확인해주세요.")
    previous = {entry["id"]: entry for entry in json.loads(Path("notices.json").read_text())["notices"]}
    legacy = json.loads(Path("legacy-notices.json").read_text())["notices"]
    publications: list[dict] = []
    failures: list[str] = []
    for page in range(1, 11):
        issues = request_json(f"repos/{repository}/issues?state=all&labels=notice&sort=created&direction=desc&per_page=100&page={page}")
        if not isinstance(issues, list):
            raise ValueError("공지 목록을 읽지 못했습니다.")
        for issue in issues:
            try:
                entry = convert(issue, repository)
                if entry is not None:
                    entry["imageUrls"] = [publish_photo(url, repository, issue["number"], index)
                        for index, url in enumerate(entry["imageUrls"], start=1)]
                    publications.append(entry)
            except (ValueError, KeyError, TypeError, OSError, urllib.error.URLError) as error:
                number = int(issue.get("number", 0))
                failures.append(f"공지 #{number}: {error}")
                old = previous.get(f"issue-{number}")
                if old is not None:
                    publications.append(old)
        if len(issues) < 100 or len(publications) >= MAX_NOTICES:
            break
    merged = publications + legacy
    merged.sort(key=lambda entry: entry["date"], reverse=True)
    document = {"version": 1, "notices": merged[:MAX_NOTICES]}
    output = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    while len(output.encode("utf-8")) > MAX_BYTES and document["notices"]:
        document["notices"].pop()
        output = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    Path("notices.json").write_text(output, encoding="utf-8")
    summary = f"게임 공지 {len(document['notices'])}개를 준비했습니다.\n"
    if failures:
        summary += "\n형식을 확인할 게시글:\n" + "\n".join("- " + error for error in failures) + "\n"
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(summary)
    print(summary)
    # 잘못된 수정은 기존 게시 내용을 보존하며 실행 결과에 알립니다.
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as error:
        print(f"공지 자동 게시에 실패했습니다: {error}", file=sys.stderr)
        sys.exit(1)
