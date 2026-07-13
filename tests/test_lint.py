from overnight.copygen.lint import ComplianceLint

L = ComplianceLint()


def test_blocks_figures():
    assert L.check_text("Coldwater pulled 4.2m last night")
    assert L.check_text("an audience of 5,000,000")
    assert L.check_text("took a 32% share")
    assert L.check_text("watched by 6 million viewers")
    assert L.check_text("a third of the country tuned in")


def test_allows_safe_numbers():
    assert L.check_text("ITV1 at 9pm — third week of growth") == []
    assert L.check_text("last night's no.1 drama, ep 4 of 6") == []
    assert L.check_text("grown for 4 weeks straight; finale at 8:30pm") == []


def test_edition_check_flags_unknown_series_and_exclaims():
    copy = {
        "subject_line": "Tonight, sorted",
        "items": [
            {"series_id": "hit", "headline": "Banker!", "body": "Great!", "chip": "Banker"},
            {"series_id": "rogue", "headline": "x", "body": "y", "chip": "Rising"},
        ],
        "whatsapp_compact": "short",
    }
    issues = L.check_edition(copy, allowed_series={"hit"})
    assert any("unknown_series" in i for i in issues)
    assert "too_many_exclamations" in issues
