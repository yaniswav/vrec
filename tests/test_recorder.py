"""Tests for vrec.recorder: result -> text/history mapping, and retry cap."""

from __future__ import annotations

from vrec import history
from vrec.recorder import (
    INCOMPLETE_REASONS,
    RecordingResult,
    StopReason,
    history_status,
    lower_quality_retry_cap,
    status_text,
)


def make_result(**kwargs) -> RecordingResult:
    defaults = dict(number=1, title="Video", image_ok=True, audio_ok=True)
    defaults.update(kwargs)
    return RecordingResult(**defaults)


def test_status_text_ended_ok() -> None:
    r = make_result(reason=StopReason.ENDED.value)
    assert status_text(r) == "OK"


def test_status_text_test_done_ok() -> None:
    r = make_result(reason=StopReason.TEST_DONE.value)
    assert status_text(r) == "OK"


def test_status_text_time_limit_is_check() -> None:
    r = make_result(reason=StopReason.TIME_LIMIT.value)
    assert status_text(r) == "CHECK: time limit reached"


def test_status_text_video_gone_is_incomplete() -> None:
    r = make_result(reason=StopReason.VIDEO_GONE.value)
    assert status_text(r) == "FAILED: incomplete (video removed from page)"


def test_status_text_stalled_is_incomplete() -> None:
    r = make_result(reason=StopReason.STALLED.value)
    assert status_text(r) == "FAILED: incomplete (loading stalled)"


def test_status_text_too_long_is_incomplete() -> None:
    r = make_result(reason=StopReason.TOO_LONG.value)
    assert status_text(r) == "FAILED: incomplete (took too long)"


def test_status_text_black_is_failed() -> None:
    r = make_result(reason=StopReason.BLACK.value)
    assert status_text(r) == "FAILED: black image (protected video?)"


def test_status_text_error_passthrough() -> None:
    r = make_result(reason="ERROR: something broke")
    assert status_text(r) == "ERROR: something broke"


def test_status_text_missing_image_only() -> None:
    r = make_result(reason=StopReason.ENDED.value, image_ok=False, audio_ok=True)
    assert status_text(r) == "CHECK: black image?"


def test_status_text_missing_audio_only() -> None:
    r = make_result(reason=StopReason.ENDED.value, image_ok=True, audio_ok=False)
    assert status_text(r) == "CHECK: no audio"


def test_status_text_missing_both_image_and_audio() -> None:
    r = make_result(reason=StopReason.ENDED.value, image_ok=False, audio_ok=False)
    assert status_text(r) == "CHECK: black image?, no audio"


def test_status_text_audio_not_checked_is_fine() -> None:
    r = make_result(reason=StopReason.ENDED.value, image_ok=True, audio_ok=None)
    assert status_text(r) == "OK"


def test_status_text_time_limit_with_missing_audio() -> None:
    r = make_result(reason=StopReason.TIME_LIMIT.value, image_ok=True, audio_ok=False)
    assert status_text(r) == "CHECK: no audio, time limit reached"


def test_history_status_ok() -> None:
    r = make_result(reason=StopReason.ENDED.value)
    assert history_status(r) == (history.STATUS_DONE, "")


def test_history_status_check() -> None:
    r = make_result(reason=StopReason.ENDED.value, image_ok=False)
    assert history_status(r) == (history.STATUS_REVIEW, "black image?")


def test_history_status_failed_incomplete() -> None:
    r = make_result(reason=StopReason.STALLED.value)
    assert history_status(r) == (history.STATUS_FAILED, "FAILED: incomplete (loading stalled)")


def test_history_status_failed_black() -> None:
    r = make_result(reason=StopReason.BLACK.value)
    assert history_status(r) == (history.STATUS_FAILED, "FAILED: black image (protected video?)")


def test_history_status_error() -> None:
    r = make_result(reason="ERROR: boom")
    assert history_status(r) == (history.STATUS_FAILED, "ERROR: boom")


def test_lower_quality_retry_cap_zero_when_not_stalled() -> None:
    r = make_result(reason=StopReason.ENDED.value, target_height=720)
    assert lower_quality_retry_cap(r, test_mode=False) == 0


def test_lower_quality_retry_cap_zero_in_test_mode() -> None:
    r = make_result(reason=StopReason.STALLED.value, target_height=720)
    assert lower_quality_retry_cap(r, test_mode=True) == 0


def test_lower_quality_retry_cap_zero_when_target_height_unknown() -> None:
    r = make_result(reason=StopReason.STALLED.value, target_height=0)
    assert lower_quality_retry_cap(r, test_mode=False) == 0


def test_lower_quality_retry_cap_returns_one_below_target() -> None:
    r = make_result(reason=StopReason.STALLED.value, target_height=720)
    assert lower_quality_retry_cap(r, test_mode=False) == 719


def test_incomplete_reasons_membership_with_plain_strings() -> None:
    assert "loading stalled" in INCOMPLETE_REASONS
    assert "video removed from page" in INCOMPLETE_REASONS
    assert "took too long" in INCOMPLETE_REASONS
    assert "ended" not in INCOMPLETE_REASONS
    assert "black image" not in INCOMPLETE_REASONS
