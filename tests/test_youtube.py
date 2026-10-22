from datetime import date

import pytest
import yt_dlp.utils

from ytt import youtube
from ytt.urls import classify
from ytt.youtube import (Aborted, DownloadError, VideoInfo, download_audio, expand,
                         friendly_error, list_channel, list_playlist, take_in_range)


def vi(id, d=None, **kw):
    return VideoInfo(id=id, title=id, url=f"https://www.youtube.com/watch?v={id}", upload_date=d, **kw)


class Boom:
    def __getattr__(self, name):
        raise AssertionError("iterated past the stop point")


def test_videoinfo_roundtrip():
    v = VideoInfo(id="a", title="T", url="u", channel="C", upload_date=date(2026, 1, 2),
                  duration=60, language="en", available=False, error="Private video")
    d = v.to_dict()
    assert d["upload_date"] == "2026-01-02"
    assert VideoInfo.from_dict(d) == v
    bare = VideoInfo(id="a", title="T", url="u")
    assert bare.to_dict()["upload_date"] is None
    assert VideoInfo.from_dict(bare.to_dict()) == bare


def test_take_in_range_stops_early():
    infos = [vi("n", date(2026, 4, 1)), vi("a", date(2026, 3, 1)), vi("b", date(2026, 1, 15)),
             vi("old", date(2025, 12, 1)), Boom()]
    got = take_in_range(iter(infos), date(2026, 1, 1), date(2026, 3, 31))
    assert [i.id for i in got] == ["a", "b"]


def test_take_in_range_undated_included_and_open_end():
    got = take_in_range([vi("x"), vi("a", date(2026, 3, 1))], date(2026, 1, 1), None)
    assert [i.id for i in got] == ["x", "a"]


def test_playlist_unavailable_entries_become_failed(fake_ydl):
    fake_ydl.playlist([{"id": "ok1", "title": "Good"}, {"id": "p1", "title": "[Private video]"},
                       {"id": "d1", "title": "[Deleted video]"}])
    infos = list_playlist("https://www.youtube.com/playlist?list=PLx")
    assert [(i.id, i.available) for i in infos] == [("ok1", True), ("p1", False), ("d1", False)]
    assert infos[1].error == "Private video"
    assert infos[2].error == "Removed or unavailable"
    assert fake_ydl.opts["extract_flat"] == "in_playlist"


def test_playlist_untitled_entry_unavailable(fake_ydl):
    fake_ydl.playlist([{"id": "z", "title": None}])
    [info] = list_playlist("u")
    assert not info.available and info.error == "Removed or unavailable"


def test_channel_stops_at_range_start(fake_ydl):
    def gen():
        yield {"id": "n", "title": "n", "upload_date": "20260401"}
        yield {"id": "a", "title": "a", "upload_date": "20260301", "channel": "Ch"}
        yield {"id": "old", "title": "old", "upload_date": "20251201"}
        raise AssertionError("crawled old history")

    fake_ydl.info = {"entries": gen()}
    got = list_channel("https://www.youtube.com/@x/videos", date(2026, 1, 1), date(2026, 3, 31))
    assert [(i.id, i.channel, i.upload_date) for i in got] == [("a", "Ch", date(2026, 3, 1))]
    assert fake_ydl.opts["extract_flat"] is False and fake_ydl.opts["lazy_playlist"] is True


def test_expand_video(fake_ydl):
    fake_ydl.info = {"id": "abc123def45", "title": "T", "uploader": "U", "duration": 12,
                     "upload_date": "20260102", "language": "en"}
    [info] = expand(classify("https://youtu.be/abc123def45"))
    assert (info.id, info.title, info.channel, info.duration, info.language) == ("abc123def45", "T", "U", 12, "en")
    assert info.upload_date == date(2026, 1, 2)


def test_expand_video_failure_is_unavailable(fake_ydl):
    fake_ydl.info = yt_dlp.utils.DownloadError("ERROR: Private video. Sign in")
    [info] = expand(classify("https://youtu.be/abc123def45"))
    assert not info.available and info.error == "Private video" and info.id == "abc123def45"


def test_expand_playlist_dispatch(fake_ydl):
    fake_ydl.playlist([{"id": "a", "title": "A"}])
    assert [i.id for i in expand(classify("https://www.youtube.com/playlist?list=PLx"))] == ["a"]


def test_expand_invalid_raises():
    with pytest.raises(DownloadError):
        expand(classify("https://example.com/x"))


@pytest.mark.parametrize("msg,friendly", [
    ("ERROR: Private video. Sign in", "Private video"),
    ("Join this channel to get access to members-only content", "Members-only"),
    ("Sign in to confirm your age", "Age-restricted"),
    ("The uploader has not made this video available in your country", "Region-blocked"),
    ("Video unavailable", "Removed or unavailable"),
])
def test_friendly_error(msg, friendly):
    assert friendly_error(DownloadError(msg)) == friendly
    assert friendly_error(yt_dlp.utils.DownloadError(msg)) == friendly
    assert friendly_error(yt_dlp.utils.ExtractorError(msg)) == friendly


def test_friendly_error_fallback_first_line_truncated():
    msg = "x" * 300 + "\nsecond"
    assert friendly_error(RuntimeError(msg)) == "x" * 200


class _DlYdl:
    def __init__(self, opts, dest, fail=None, hook_calls=1):
        self.opts, self.dest, self.fail, self.hook_calls = opts, dest, fail, hook_calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        if self.fail:
            raise self.fail
        for _ in range(self.hook_calls):
            for h in self.opts["progress_hooks"]:
                h({"status": "downloading"})
        (self.dest / "vid.webm").write_bytes(b"a")
        return {}


def _patch(monkeypatch, tmp_path, **kw):
    seen = {}

    def factory(opts):
        seen["opts"] = opts
        return _DlYdl(opts, tmp_path, **kw)

    monkeypatch.setattr(youtube, "_ydl", factory)
    return seen


def test_download_audio_returns_path_and_opts(monkeypatch, tmp_path):
    seen = _patch(monkeypatch, tmp_path)
    assert download_audio("vid", tmp_path, lambda: False) == tmp_path / "vid.webm"
    o = seen["opts"]
    assert o["format"] == "bestaudio/best" and o["quiet"] and o["noprogress"]
    assert o["outtmpl"] == str(tmp_path / "%(id)s.%(ext)s")


def test_download_audio_abort(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    with pytest.raises(Aborted):
        download_audio("vid", tmp_path, lambda: True)


def test_download_audio_error_is_friendly(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, fail=yt_dlp.utils.DownloadError("ERROR: Private video"))
    with pytest.raises(DownloadError) as e:
        download_audio("vid", tmp_path, lambda: False)
    assert e.value.reason == "Private video"


@pytest.mark.live
def test_live_expand_playlist():
    # Short public playlist (2 videos, verified with yt-dlp 2026-10-22); adjust if it disappears.
    infos = expand(classify("https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"))
    assert infos and all(i.id for i in infos)
