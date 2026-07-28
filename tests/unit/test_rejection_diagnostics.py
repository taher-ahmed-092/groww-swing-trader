"""Scan jobs log the actual blocking gate + reason for rejected candidates
instead of a generic 'no candidate cleared the pipeline' message."""
from __future__ import annotations

import runner


def test_log_top_rejections_shows_up_to_three(capsys):
    rejections = [
        runner._rejection_detail("BIOCON", "HARD_REJECTED_FUNDAMENTAL", "ROCE 3.61%"),
        runner._rejection_detail("GLAND", "JUDGE", "judge 5.2<5.5"),
        runner._rejection_detail("CIPLA", "TECHNICAL", "score 0.40 < 0.80"),
        runner._rejection_detail("SUNPHARMA", "RISK", "sector cap"),
    ]
    runner._log_top_rejections("market_open_scan_job", 10, rejections)
    out = capsys.readouterr().out
    assert "0/10 cleared" in out
    assert "BIOCON" in out and "HARD_REJECTED_FUNDAMENTAL" in out and "ROCE 3.61%" in out
    assert "GLAND" in out and "judge 5.2<5.5" in out
    assert "CIPLA" in out
    # Only the top 3 are surfaced, not the 4th.
    assert "SUNPHARMA" not in out


def test_log_top_rejections_all_cleared(capsys):
    runner._log_top_rejections("market_open_scan_job", 5, [])
    out = capsys.readouterr().out
    assert "5/5 cleared" in out
