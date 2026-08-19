"""Tests for FruitDeepLinks / scheme → Android package catalog."""

from __future__ import annotations

from apituner.deeplink_catalog import (
    catalog_payload,
    infer_provider,
    packages_for,
    parse_lane_url,
    resolve_packages,
)


def test_yttv_profile_packages():
    google = packages_for("yttv", profile="google_tv")
    fire = packages_for("yttv", profile="fire")
    assert google == (
        "com.google.android.youtube.tvunplugged",
        "com.amazon.firetv.youtube.tv",
    )
    assert fire == (
        "com.amazon.firetv.youtube.tv",
        "com.google.android.youtube.tvunplugged",
    )


def test_profile_for_device_amazon_and_chromecast():
    from apituner.deeplink_catalog import profile_for_device

    assert (
        profile_for_device(manufacturer="Amazon", fallback="google_tv") == "fire"
    )
    assert profile_for_device(model="AFTSSS", fallback="google_tv") == "fire"
    assert profile_for_device(keys_type="firetv_rest", fallback="google_tv") == "fire"
    assert (
        profile_for_device(manufacturer="Google", fallback="fire") == "google_tv"
    )
    assert profile_for_device(fallback="google_tv") == "google_tv"


def test_merge_package_try_order_channel_first():
    from apituner.deeplink_catalog import merge_package_try_order

    order = merge_package_try_order(
        ["com.amazon.firetv.youtube.tv"],
        (
            "com.google.android.youtube.tvunplugged",
            "com.amazon.firetv.youtube.tv",
        ),
    )
    assert order[0] == "com.amazon.firetv.youtube.tv"
    assert order[1] == "com.google.android.youtube.tvunplugged"


def test_espn_profile_packages():
    google = packages_for("sportscenter", profile="google_tv")
    fire = packages_for("sportscenter", profile="fire")
    assert google == ("com.espn.score_center", "com.espn.gtv")
    assert fire == ("com.espn.gtv", "com.espn.score_center")


def test_scheme_and_https_infer_provider():
    assert infer_provider("sportscenter://x-callback-url/showWatchStream?playID=1") == (
        "sportscenter"
    )
    assert infer_provider("https://play.hbomax.com/sport/abc") == "max"
    assert infer_provider("aiv://aiv/detail?gti=x") == "aiv"


def test_lane_path_parse():
    url = "http://192.0.2.40:6655/api/adb/lanes/sportscenter/1/deeplink?format=text"
    assert parse_lane_url(url) == ("sportscenter", 1)
    assert infer_provider(url) == "sportscenter"


def test_max_same_package_both_profiles():
    google = packages_for("max", profile="google_tv")
    fire = packages_for("max", profile="fire")
    assert google == ("com.wbd.stream", None)
    assert fire == ("com.wbd.stream", None)


def test_explicit_package_wins():
    code, pkg, alt = resolve_packages(
        url="sportscenter://x",
        package_name="com.custom.espn",
        alternate_package_name="com.other",
        profile="fire",
    )
    assert code == "sportscenter"
    assert pkg == "com.custom.espn"
    assert alt == "com.other"


def test_code_from_display_name():
    from apituner.deeplink_catalog import code_from_display_name

    assert code_from_display_name("ESPN") == "sportscenter"
    assert code_from_display_name("Prime Video") == "aiv"
    assert code_from_display_name("Apple TV") == "apple_other"
    assert code_from_display_name("MLB") == "mlb"
    payload = catalog_payload()
    assert payload["profiles"] == ["google_tv", "fire"]
    sports = next(p for p in payload["providers"] if p["code"] == "sportscenter")
    assert "sportscenter" in sports["schemes"]
    assert sports["packages"]["google_tv"]["package_name"] == "com.espn.score_center"
    assert sports["packages"]["fire"]["package_name"] == "com.espn.gtv"
