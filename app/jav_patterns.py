from __future__ import annotations


DEFAULT_JAV_NAME_REGEX = (
    r"(?ix)"
    r"(?<![a-z0-9])"
    r"(?:"
    r"FC2(?:[-_.\s]*(?:PPV|PPVDB))?[-_.\s]*\d{5,8}"
    r"|HEYZO(?:[-_.\s]*HD)?[-_.\s]*\d{3,5}"
    r"|1PONDO[-_.\s]*\d{6}[-_.\s]*\d{3}"
    r"|CARIB(?:BEANCOM)?[-_.\s]*\d{6}[-_.\s]*\d{3}"
    r"|TOKYO[-_.\s]*HOT[-_.\s]*N[-_.\s]*\d{3,5}"
    r"|[a-z]{2,8}[-_.\s]*\d{2,5}"
    r")"
    r"(?![a-z0-9]|\.\d)"
)
