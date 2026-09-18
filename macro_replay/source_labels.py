from __future__ import annotations


SOURCE_LABEL_OVERRIDES = {
    "manual-excel-term": "WIND",
    "MANUAL-EXCEL-TERM": "WIND",
    "manual-excel-line": "WIND",
    "MANUAL-EXCEL-LINE": "WIND",
    "manual-excel-multi-line": "WIND",
    "MANUAL-EXCEL-MULTI-LINE": "WIND",
    "akshare_sina_foreign_xau": "AKSHARE / SINA XAU",
    "AKSHARE_SINA_FOREIGN_XAU": "AKSHARE / SINA XAU",
}


def display_source_label(source: str | None) -> str:
    if not source:
        return ""
    return SOURCE_LABEL_OVERRIDES.get(source, SOURCE_LABEL_OVERRIDES.get(source.upper(), source.upper()))
