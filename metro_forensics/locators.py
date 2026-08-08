"""Exact, reproducible Level-1 source locator validation."""

import re


_EXACT_LOCATORS = (
    re.compile(r"page:[1-9]\d*(?:-[1-9]\d*)?"),
    re.compile(r"sheet:[^!:\n]+![A-Za-z]+[1-9]\d*(?::[A-Za-z]+[1-9]\d*)?"),
    re.compile(r"paragraph:[1-9]\d*(?:-[1-9]\d*)?"),
    re.compile(
        r"table:[1-9]\d*(?:-[1-9]\d*)?"
        r"(?:,row:[1-9]\d*(?:-[1-9]\d*)?)?"
        r"(?:,cell:[1-9]\d*(?:-[1-9]\d*)?)?"
    ),
    re.compile(r"whole-file"),
)


def is_exact_locator(locator: object) -> bool:
    return isinstance(locator, str) and any(
        pattern.fullmatch(locator) for pattern in _EXACT_LOCATORS
    )


def require_exact_locator(locator: object) -> str:
    if not is_exact_locator(locator):
        raise ValueError("locator must be an exact Level 1 locator")
    return locator
