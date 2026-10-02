"""Finnhub provider adapter -- earnings only.

Massive's own earnings endpoint (Benzinga) is entitlement-blocked under the
configured plan (HTTP 403); this adapter exists to cover earnings only, and
does not touch reference or daily-bar data, which stay on Massive.
"""
