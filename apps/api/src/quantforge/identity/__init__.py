"""Historical listing identity: what ticker a security traded under on a date, and how well that is proven.

Two kinds of evidence exist and are never conflated:

* **provisional, first-observed** -- a ``Listing`` row: QuantForge saw this ticker on this venue no
  later than ``valid_from``. It proves nothing about earlier dates.
* **source-stated history** -- ``ListingHistoryFact`` rows: a provider states what held on a date.

:mod:`quantforge.identity.listing_history` derives intervals from them (pure, deterministic,
re-runnable) and :mod:`quantforge.identity.resolver` is the ONE resolver every consumer uses.
Listing identity is reference-data reconstruction; it is not availability evidence and never feeds
``observed_at`` or any point-in-time signal claim.
"""
