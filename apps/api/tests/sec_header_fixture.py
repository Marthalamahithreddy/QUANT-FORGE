"""Offline EDGAR filing headers: real acceptance values, minimal synthetic surroundings."""

from __future__ import annotations


def header(
    *,
    accession: str = "0000320187-26-000037",
    acceptance: str = "20260401161639",
    form: str = "10-Q",
    filing_date: str = "20260401",
    ciks: tuple[str, ...] = ("0000320187",),
    eol: str = "\n",
) -> bytes:
    filers = "".join(
        f"<FILER>{eol}<COMPANY-DATA>{eol}<CONFORMED-NAME>EXAMPLE INC{eol}<CIK>{cik}{eol}</COMPANY-DATA>{eol}</FILER>{eol}"
        for cik in ciks
    )
    return (
        f"<SEC-HEADER>{accession}.hdr.sgml : {filing_date}{eol}"
        f"<ACCEPTANCE-DATETIME>{acceptance}{eol}"
        f"<ACCESSION-NUMBER>{accession}{eol}"
        f"<TYPE>{form}{eol}"
        f"<PUBLIC-DOCUMENT-COUNT>71{eol}"
        f"<PERIOD>20260228{eol}"
        f"<FILING-DATE>{filing_date}{eol}"
        f"{filers}"
        f"</SEC-HEADER>{eol}"
    ).encode()
