import pytest

from ytt.urls import classify, split_urls, video_url


@pytest.mark.parametrize("url,kind,norm", [
    ("https://youtu.be/abc123def45", "video", "https://www.youtube.com/watch?v=abc123def45"),
    ("https://www.youtube.com/shorts/abc123def45", "video", "https://www.youtube.com/watch?v=abc123def45"),
    ("https://www.youtube.com/live/abc123def45", "video", "https://www.youtube.com/watch?v=abc123def45"),
    ("https://www.youtube.com/watch?v=abc123def45&list=PLx", "video", "https://www.youtube.com/watch?v=abc123def45"),
    ("https://www.youtube.com/playlist?list=PLx", "playlist", "https://www.youtube.com/playlist?list=PLx"),
    ("https://www.youtube.com/@TechWorld/streams", "channel", "https://www.youtube.com/@TechWorld/videos"),
    ("https://www.youtube.com/channel/UC123", "channel", "https://www.youtube.com/channel/UC123/videos"),
    ("https://www.youtube.com/c/Name/featured", "channel", "https://www.youtube.com/c/Name/videos"),
    ("https://www.youtube.com/user/Name", "channel", "https://www.youtube.com/user/Name/videos"),
    ("https://example.com/x", "invalid", None),
    ("not a url", "invalid", None),
    ("https://www.youtube.com/", "invalid", None),
])
def test_classify(url, kind, norm):
    parsed = classify(url)
    assert parsed.kind == kind
    if norm:
        assert parsed.url == norm


def test_classify_video_id():
    assert classify("https://youtu.be/abc123def45").video_id == "abc123def45"
    assert classify("https://www.youtube.com/@x").video_id is None


def test_video_url():
    assert video_url("abc") == "https://www.youtube.com/watch?v=abc"


def test_split_urls_messy():
    text = "# my list\nhttps://youtu.be/a1, https://youtu.be/b2  https://youtu.be/a1\n\n"
    assert split_urls(text) == ["https://youtu.be/a1", "https://youtu.be/b2"]
