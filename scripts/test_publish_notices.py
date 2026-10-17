import copy
import unittest

from publish_notices import convert, image_url

REPOSITORY = "TopFiddlesticksGap/Where-Whale-Notices"
PHOTO = "https://github.com/user-attachments/assets/12345678"


def issue(content="새 공지입니다.", state="게시"):
    return {"number": 3, "state": "open", "author_association": "MEMBER", "title": "새로운 소식",
            "created_at": "2026-10-17T23:30:00Z", "labels": [{"name": "notice"}],
            "body": f"### 본문\n\n{content}\n\n### 게시 상태\n\n{state}"}


class PublishingTests(unittest.TestCase):
    def test_text_date_and_stable_id(self):
        result = convert(issue(), REPOSITORY)
        self.assertEqual(result["date"], "2026-10-18")
        self.assertEqual(result["id"], "issue-3")
        self.assertEqual(result["body"], "새 공지입니다.")

    def test_markdown_photo(self):
        result = convert(issue(f"본문입니다.\n\n![사진]({PHOTO})"), REPOSITORY)
        self.assertEqual(result["imageUrls"], [PHOTO])
        self.assertNotIn("![", result["body"])

    def test_html_photo(self):
        result = convert(issue(f'본문입니다.\n<img width="1200" src="{PHOTO}" alt="사진" />'), REPOSITORY)
        self.assertEqual(result["imageUrls"], [PHOTO])
        self.assertNotIn("<img", result["body"])

    def test_duplicate_photos(self):
        result = convert(issue(f"본문\n![a]({PHOTO})\n![b]({PHOTO})"), REPOSITORY)
        self.assertEqual(len(result["imageUrls"]), 1)

    def test_video_url_and_mp3_link(self):
        url = "https://github.com/user-attachments/files/123/music.mp3"
        result = convert(issue(f"본문\n{PHOTO}\n[소리]({url})"), REPOSITORY)
        self.assertEqual(result["imageUrls"], [url, PHOTO])
        self.assertNotIn("https://", result["body"])

    def test_attachment_without_body_text(self):
        result = convert(issue(PHOTO), REPOSITORY)
        self.assertEqual(result["imageUrls"], [PHOTO])
        self.assertEqual(result["body"], "첨부 파일을 확인해주세요.")

    def test_html_video(self):
        result = convert(issue(f'본문\n<video src="{PHOTO}"></video>'), REPOSITORY)
        self.assertEqual(result["imageUrls"], [PHOTO])
        self.assertEqual(result["body"], "본문")

    def test_draft_is_not_published(self):
        self.assertIsNone(convert(issue(state="임시저장"), REPOSITORY))

    def test_closed_is_not_published(self):
        value = issue(); value["state"] = "closed"
        self.assertIsNone(convert(value, REPOSITORY))

    def test_outsider_is_not_published(self):
        value = issue(); value["author_association"] = "CONTRIBUTOR"
        self.assertIsNone(convert(value, REPOSITORY))

    def test_approved_friend_without_org_membership(self):
        value = issue(); value["author_association"] = "NONE"; value["user"] = {"login": "Friend"}
        self.assertIsNotNone(convert(value, REPOSITORY, ["friend"]))
        self.assertIsNone(convert(value, REPOSITORY, ["someone-else"]))

    def test_approved_friend_still_needs_label_and_publication(self):
        value = issue(state="임시저장"); value["author_association"] = "NONE"; value["user"] = {"login": "Friend"}
        self.assertIsNone(convert(value, REPOSITORY, ["friend"]))
        value = issue(); value["author_association"] = "NONE"; value["user"] = {"login": "Friend"}; value["labels"] = []
        self.assertIsNone(convert(value, REPOSITORY, ["friend"]))

    def test_unlabelled_is_not_published(self):
        value = issue(); value["labels"] = []
        self.assertIsNone(convert(value, REPOSITORY))

    def test_pull_request_is_not_published(self):
        value = issue(); value["pull_request"] = {"url": "https://example.com"}
        self.assertIsNone(convert(value, REPOSITORY))

    def test_six_photos_are_rejected(self):
        with self.assertRaises(ValueError):
            convert(issue("본문\n" + "\n".join(f"![사진]({PHOTO}{n})" for n in range(6))), REPOSITORY)

    def test_oversized_text_is_rejected(self):
        with self.assertRaises(ValueError):
            convert(issue("가" * 8001), REPOSITORY)

    def test_blank_body_is_rejected(self):
        with self.assertRaises(ValueError):
            convert(issue("_No response_"), REPOSITORY)

    def test_external_and_credential_urls_are_rejected(self):
        for url in ["http://github.com/image.png", "https://example.com/image.png", "https://user:password@github.com/user-attachments/assets/a"]:
            with self.assertRaises(ValueError):
                image_url(url, REPOSITORY)

    def test_input_is_not_mutated(self):
        value = issue(); original = copy.deepcopy(value)
        convert(value, REPOSITORY)
        self.assertEqual(value, original)

    def test_markdown_readability(self):
        result = convert(issue("## 새 소식\n\n**본문**입니다.\n\n[안내](https://example.com)"), REPOSITORY)
        self.assertEqual(result["body"], "새 소식\n\n본문입니다.\n\n안내 (https://example.com)")


if __name__ == "__main__":
    unittest.main()
