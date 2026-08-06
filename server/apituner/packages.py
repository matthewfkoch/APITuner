"""Installed-package helpers shared by tune selection and the dashboard."""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

# Same app, different store listings — prefer whichever is installed.
PACKAGE_FAMILY_ALTERNATES: dict[str, tuple[str, ...]] = {
    "com.espn.gtv": ("com.espn.score_center",),
    "com.espn.score_center": ("com.espn.gtv",),
}


def package_candidates(
    package_name: Optional[str],
    alternate_package_name: Optional[str] = None,
) -> list[str]:
    """Primary, alternate, then known family swaps (deduped, order preserved)."""
    candidates: list[str] = []
    for pkg in (package_name, alternate_package_name):
        if pkg and pkg not in candidates:
            candidates.append(pkg)
    for pkg in list(candidates):
        for alt in PACKAGE_FAMILY_ALTERNATES.get(pkg, ()):
            if alt not in candidates:
                candidates.append(alt)
    return candidates


def package_try_order(
    package_name: Optional[str],
    alternate_package_name: Optional[str] = None,
    *,
    installed: Optional[Sequence[str]] = None,
) -> list[str]:
    """Ordered packages to try for launch: known-installed first, then the rest.

    When ``installed`` is unknown/empty, returns the full candidate list so
    alternate / family packages are still attempted after a launch failure.
    """
    candidates = package_candidates(package_name, alternate_package_name)
    if not candidates:
        return []
    if not installed:
        return candidates
    have = set(installed)
    preferred = [p for p in candidates if p in have]
    rest = [p for p in candidates if p not in have]
    return preferred + rest if preferred else candidates


def package_installed(installed: Iterable[str], *wanted: Optional[str]) -> bool:
    """True when any candidate for the wanted packages is in ``installed``."""
    have = set(installed)
    if not have:
        return False
    for pkg in wanted:
        if not pkg:
            continue
        for candidate in package_candidates(pkg):
            if candidate in have:
                return True
    return False
