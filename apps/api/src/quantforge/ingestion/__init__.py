"""Provider-to-canonical ingestion pipeline.

Ingests ticker reference data and daily market bars, preserves the provider's
raw responses verbatim, and records every run's provenance so that any stored
fact can be traced to the bytes that produced it.
"""
