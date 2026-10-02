"""Market-data providers and their error/pacing/shape contracts.

A provider implements :class:`quantforge.providers.base.MarketDataProvider`
and brings its own adapter package (``massive`` today). The ingestion pipeline
consumes only the protocol, the canonical DTOs and the documented errors, so a
second provider can be added without touching the pipeline.

Capabilities stay separable: an earnings source implements the narrowly scoped
:class:`quantforge.providers.earnings.EarningsProvider` (defined for a later
milestone; no adapter implements it yet) instead of being forced to also speak
market data.
"""
