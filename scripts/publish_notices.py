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

from notice_media import publish_attachment
import subprocess

MAX_BYTES = 256 * 1024
MAX_NOTICES = 50
STAFF = {"OWNER", "MEMBER", "COLLABORATOR"}
# GitHub가 첨부 업로드에 실패하거나 업로드 중에 저장하면 본문에 파일 대신 남기는 표시입니다.
FAILED_UPLOAD = re.compile(r'<!--\s*Failed to upload "([^"]+)"\s*-->|!\[Uploading ([^\]]+?)(?:…|\.\.\.)?\]\(\)')
MARKDOWN_IMAGE = re.compile(r'!\[[^\]]*\]\(\s*(https://[^\s)]+)(?:\s+["\'][^"\']*["\'])?\s*\)')


class _UrlMatch:
    def __init__(self, value): self.value = value
    def group(self, _): return self.value


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"img", "video", "audio", "source"}:
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
        host == "github.com" and parsed.path.startswith(("/user-attachments/assets/", "/user-attachments/files/"))
    ) or (host == "raw.githubusercontent.com" and parsed.path.startswith("/" + repository + "/main/images/"))
    if not allowed:
        raise ValueError("외부 사진 주소 대신 본문 입력창에 사진을 첨부해주세요.")
    return value


def convert(issue: dict, repository: str, approved_users: tuple | list = ()) -> dict | None:
    login = (issue.get("user", {}).get("login") or "").casefold()
    approved = login in {user.casefold() for user in approved_users}
    if issue.get("pull_request") or issue.get("state") != "open" or (issue.get("author_association") not in STAFF and not approved):
        return None
    if "notice" not in {label["name"] for label in issue.get("labels", [])}:
        return None
    form = issue.get("body") or ""
    if section(form, "게시 상태") != "게시":
        return None
    content = section(form, "본문")
    if not content or content == "_No response_":
        raise ValueError("본문을 입력해주세요.")
    failed = [first or second for first, second in FAILED_UPLOAD.findall(content)]
    if failed:
        raise ValueError(f"첨부 파일 {', '.join(failed)}이(가) GitHub에 올라가지 않았습니다. "
            "게시글을 수정해 파일을 다시 첨부하고, 미리보기에서 보이는지 확인한 뒤 저장해주세요. 사진과 GIF는 10MB 이하여야 합니다.")
    urls: list[str] = []

    def replace_image(match: re.Match) -> str:
        urls.append(image_url(match.group(1), repository))
        return "\n"

    content = MARKDOWN_IMAGE.sub(replace_image, content)
    # GitHub는 MP4를 단독 URL, MP3를 파일 링크로 본문에 넣습니다.
    attachment = r'https://github\.com/user-attachments/(?:assets|files)/[^\s<>"\')]+|https://user-images\.githubusercontent\.com/[^\s<>"\')]+\.gif'
    def replace_attachment(match):
        urls.append(image_url(match.group(0), repository))
        return ""
    content = re.sub(r'\[[^\]]*\]\((' + attachment + r')\)', lambda m: replace_attachment(_UrlMatch(m.group(1))), content)
    # HTML 태그의 src는 아래 HTMLParser가 처리합니다.
    content = re.sub(r'(?<!["\'])' + attachment, replace_attachment, content)
    parser = PlainText()
    parser.feed(content)
    parser.close()
    urls.extend(image_url(url, repository) for url in parser.images)
    urls = list(dict.fromkeys(urls))
    if len(urls) > 5:
        raise ValueError("첨부 파일은 합쳐서 최대 5개까지 올려주세요.")
    text = "".join(parser.parts)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\((https://[^\s)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text and urls:
        text = "첨부 파일을 확인해주세요."
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
    approved_users = json.loads(Path("publishers.json").read_text()).get("users", [])
    if not isinstance(approved_users, list) or any(not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", user) for user in approved_users):
        raise ValueError("publishers.json의 공지 작성자 계정을 확인해주세요.")
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
                entry = convert(issue, repository, approved_users)
                if entry is not None:
                    sources = entry["imageUrls"]
                    entry["imageUrls"], entry["media"] = [], []
                    for index, url in enumerate(sources, start=1):
                        kind, asset = publish_attachment(url, repository, issue["number"], index)
                        if kind == "image": entry["imageUrls"].append(asset)
                        else: entry["media"].append(asset)
                    publications.append(entry)
            except (ValueError, KeyError, TypeError, OSError, urllib.error.URLError, subprocess.SubprocessError) as error:
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
