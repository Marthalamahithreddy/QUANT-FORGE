"""Turn a canonical reference into the stored rows that name it.

The problem
-----------
A provider hands over a symbol and a handful of identifiers. Storage needs a
graph: an issuer, an instrument, a venue, and a listing tying the symbol to the
instrument on that venue. Resolving one into the other is where ingestion either
becomes safe or quietly corrupts a dataset, because the two failure modes look
identical in the output:

*Resolving too eagerly* merges distinct companies. Two issuers that share a
legal name -- entirely possible across jurisdictions, and common in the
directories that provider reference data is scraped from -- collapse into one row,
and one company's entire price history is then attached to the other. Nothing
downstream can detect this, because the merged row looks perfectly normal.

*Resolving too lazily* duplicates one company across many rows, which fragments
the history and quietly turns every per-issuer aggregate into a partial one.

The rule here is: **only a stable, provider-asserted identifier may create a
match.** CIK first, then FIGI. A name is never enough. When neither identifier is
present the resolver refuses, because there is no way to distinguish "brand new
issuer" from "issuer we already hold under a different name" -- and guessing
wrong in that direction is a silent merge.

What is deliberately not implemented
------------------------------------
*Ticker reuse.* A symbol can be retired and reissued to a different issuer. When
the current listing for ``(venue, ticker)`` already points at a different
security, that is exactly this situation, and the resolver raises
:class:`TickerReuseConflictError` rather than re-pointing the existing listing. The
interval model in :class:`~quantforge.models.security.Listing` can express the
correct answer (close the old interval, open a new one), but deciding *when* a
reissue happened is a judgement about history that a single vendor's current
symbol table cannot settle. An operator resolves it explicitly; a heuristic does
not.

*Fuzzy name matching.* Not implemented, not planned for the automatic path. The
:func:`~quantforge.repositories.company.CompanyRepository.search_by_display_name`
prefix search exists for an operator resolving an ambiguity by hand, which is a
different activity from automated ingestion.

valid_from policy
-----------------
A provider's reference table describes the ticker *now*; it does not say when the
listing began trading. Massa's reference data has no listing-start date, and
inventing one -- from an IPO date the provider does not expose, or from the
earliest bar available -- would put a boundary in the historical data that no
source attests to. So a newly created listing gets ``valid_from = the date we
first observed it``, and that limitation is recorded in the listing's own row
rather than left for someone to rediscover. Practically it means
"listed as of the first time we saw it", which is the strongest claim the
available evidence supports.

Concurrency and isolation
--------------------------
Every get-or-create below is one atomic ``INSERT ... ON CONFLICT DO NOTHING``
against the real unique index, not a ``SELECT`` then ``INSERT``: two concurrent
resolutions of the same identity converge on one row instead of the loser
raising an unhandled ``IntegrityError`` that aborts the rest of its ingestion
batch. This assumes the default ``READ COMMITTED`` isolation every CLI
ingestion run actually uses. It is *not* a safe pattern under ``REPEATABLE
READ`` or ``SERIALIZABLE``: PostgreSQL can raise ``ON CONFLICT``'s internal
conflict check as a bare serialization failure under those levels instead of
quietly reporting "no row inserted" (see
:mod:`quantforge.ingestion.sources`, whose get-or-create uses the older
catch-the-``IntegrityError`` form for exactly this reason, pinned by
``test_a_lost_insert_reuses_the_winners_row``). A caller that ever runs this
resolver under a stricter isolation level must be prepared to retry the whole
transaction on a serialization failure, the same as it would for any other
write under that level.
"""

from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import (
    CanonicalEquityReference,
    ReferenceResolution,
)
from quantforge.models.company import Company
from quantforge.models.enums import IdentifierType, SecurityType
from quantforge.models.security import (
    Exchange,
    Listing,
    Security,
    SecurityIdentifier,
)

#: The reference adapter stores the SHARE-CLASS FIGI, so new rows are written as
#: ``FIGI_SHARE_CLASS``. Lookups also match legacy ``FIGI`` rows (level unspecified) purely to avoid
#: creating a duplicate security for a code that is already stored; a legacy row is never trusted as
#: a share-class anchor elsewhere (``quantforge.identity``).
_FIGI_TYPES = (IdentifierType.FIGI_SHARE_CLASS, IdentifierType.FIGI)

__all__ = [
    "ReferenceResolutionError",
    "ReferenceResolver",
    "TickerReuseConflictError",
    "UnresolvableReferenceError",
]


class ReferenceResolutionError(RuntimeError):
    """Base class for reference-resolution refusals."""


class UnresolvableReferenceError(ReferenceResolutionError):
    """The reference carries no identifier that can safely key a stored issuer."""


class TickerReuseConflictError(ReferenceResolutionError):
    """The ticker is already listed on this venue for a different security.

    Raised rather than resolved. See the module docstring: deciding when a
    symbol was reissued is a judgement about history, and automating it from a
    current symbol table would rewrite the past on the strength of a present-day
    observation.
    """

    def __init__(
        self,
        *,
        ticker: str,
        mic: str,
        existing_security_id: uuid.UUID,
        incoming_security_id: uuid.UUID,
    ) -> None:
        self.ticker = ticker
        self.mic = mic
        self.existing_security_id = existing_security_id
        self.incoming_security_id = incoming_security_id
        super().__init__(
            f"{ticker} on {mic} is already listed against security "
            f"{existing_security_id}, but this reference resolves to "
            f"{incoming_security_id}. That is the signature of a ticker being "
            "retired and reissued, which ingestion will not decide on its own: "
            "close the old listing interval and open a new one deliberately, or "
            "confirm the reference is wrong."
        )


#: Display label per instrument class, used to synthesise a stable security name
#: when the provider supplies no instrument-level name of its own.
_SECURITY_TYPE_LABEL: dict[str, str] = {
    SecurityType.EQUITY: "Common Stock",
    SecurityType.PREFERRED_EQUITY: "Preferred Stock",
}

#: QuantForge's vocabulary back to the model enum. The canonical layer carries
#: plain strings so it stays free of ORM imports; this is the single place the
#: two are reconciled.
_SECURITY_TYPE_ENUM: dict[str, SecurityType] = {
    SecurityType.EQUITY: SecurityType.EQUITY,
    SecurityType.PREFERRED_EQUITY: SecurityType.PREFERRED_EQUITY,
}


class ReferenceResolver:
    """Creates or reuses the issuer/instrument/venue/listing rows for a reference.

    Takes a :class:`~sqlalchemy.orm.Session` and never commits. The caller owns
    the transaction, so a whole reference -- and the run that recorded it -- can
    be committed or rolled back together.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        reference: CanonicalEquityReference,
        *,
        source_id: uuid.UUID,
        as_of: date,
    ) -> ReferenceResolution:
        """Resolve one reference, creating only what is genuinely missing.

        Args:
            reference: The provider-independent reference to resolve.
            source_id: The :class:`~quantforge.models.provenance.DataSource` the
                reference came from, recorded on any identifier created.
            as_of: The date the observation was made, used as the ``valid_from``
                of a newly created listing. See the module docstring on why the
                true listing start is not invented.

        Returns:
            A :class:`ReferenceResolution` naming the stored rows, and whether
            anything was created.

        Raises:
            UnresolvableReferenceError: No stable identifier was available.
            TickerReuseConflictError: The ticker is live on another security.
        """
        created = False

        exchange, exchange_created = self._resolve_exchange(reference)
        created |= exchange_created

        # No source_id here: a Company row has no provenance column. Provenance
        # for a reference lives on the identifier rows and the IngestionRun,
        # which is where "which vendor told us this" is actually needed.
        company, company_created, matched_on = self._resolve_company(reference)
        created |= company_created

        security, security_created = self._resolve_security(
            reference, company_id=company.id, source_id=source_id
        )
        created |= security_created

        listing, listing_created = self._resolve_listing(
            reference,
            security_id=security.id,
            exchange_id=exchange.id,
            as_of=as_of,
        )
        created |= listing_created

        return ReferenceResolution(
            reference=reference,
            listing_id=listing.id,
            security_id=security.id,
            company_id=company.id,
            matched_on=matched_on,
            created=created,
        )

    # ── Exchange ───────────────────────────────────────────────────────────

    def _resolve_exchange(self, reference: CanonicalEquityReference) -> tuple[Exchange, bool]:
        """Get or create the venue by MIC.

        The MIC is the identity, and it is globally assigned, so this is a single
        indexed lookup with no ambiguity. Reference data is the only place a
        venue is learned from, so the first resolution of any symbol is also what
        populates the venue table.

        The get-or-create is one atomic ``INSERT ... ON CONFLICT DO NOTHING``
        rather than a plain ``SELECT`` then ``INSERT``. Two ingestion runs
        resolving the same venue at once both see "absent" under the naive
        form, both insert, and the loser dies on ``mic``'s unique index with a
        raw ``IntegrityError`` that aborts everything else in its transaction
        -- the whole point of this milestone's idempotency guarantee failing
        under exactly the concurrency it is supposed to survive. With the
        conflict clause the loser's insert is silently a no-op and it falls
        back to reading the row the winner created.
        """
        venue = reference.venue
        if venue is None:
            message = (
                f"{reference.ticker}: reference carries no venue, so there is "
                "nothing to attach a listing to"
            )
            raise UnresolvableReferenceError(message)

        existing = self._session.scalar(sa.select(Exchange).where(Exchange.mic == venue.mic))
        if existing is not None:
            return existing, False

        exchange_id = uuid.uuid4()
        statement = (
            postgresql.insert(Exchange)
            .values(
                id=exchange_id,
                mic=venue.mic,
                name=venue.name,
                # The adapter's table is the authority for a venue's identity;
                # the fields below are left to their defaults rather than
                # invented here, because this resolver is given a venue with
                # exactly three fields and fabricating a country code would be
                # a guess.
                timezone=venue.timezone_name,
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=["mic"])
            .returning(Exchange.id)
        )
        result = self._session.execute(statement)
        inserted_id = result.scalar_one_or_none()
        if inserted_id is None:
            # Lost the race: the concurrent writer's row is the one to use.
            winner = self._session.scalar(sa.select(Exchange).where(Exchange.mic == venue.mic))
            if winner is None:  # pragma: no cover - unreachable under READ COMMITTED
                message = f"ON CONFLICT reported mic {venue.mic!r} as held, but no committed row was visible"
                raise RuntimeError(message)
            return winner, False

        exchange = self._session.get(Exchange, inserted_id)
        assert exchange is not None
        return exchange, True

    # ── Company ────────────────────────────────────────────────────────────

    def _resolve_company(self, reference: CanonicalEquityReference) -> tuple[Company, bool, str]:
        """Find or create the issuer, keyed on CIK and only on CIK.

        A company row needs ``legal_name`` and ``display_name``, neither of which
        a reference payload reliably supplies -- providers publish a trading or
        marketing name, not a registered legal one. So the *name* of a new
        company is filled from the reference, and clearly marked as such by
        having no CIK-independent claim to be authoritative. What makes the row
        trustworthy is the CIK, and the name is a label that can be corrected
        later by an authoritative source.

        Matching never consults the name. That is the whole point: a name match
        would merge two issuers.

        Like the exchange lookup, the creation branch is one atomic
        ``INSERT ... ON CONFLICT DO NOTHING`` against ``uq_company_cik``
        rather than a plain ``SELECT`` then ``INSERT``, so two concurrent
        resolutions of the same CIK converge on one row instead of the loser
        dying on the unique index with an unhandled ``IntegrityError``.
        """
        if reference.cik is not None:
            existing = self._session.scalar(sa.select(Company).where(Company.cik == reference.cik))
            if existing is not None:
                return existing, False, f"cik:{reference.cik}"

            display_name = reference.company_name or reference.ticker
            company_id = uuid.uuid4()
            statement = (
                postgresql.insert(Company)
                .values(
                    id=company_id,
                    # No legal name is available from a trading reference, so
                    # the display name stands in. It is not unique and not
                    # matched on, so nothing downstream depends on it being
                    # the registered name.
                    legal_name=display_name,
                    display_name=display_name,
                    cik=reference.cik,
                    country_code="US",
                )
                .on_conflict_do_nothing(
                    index_elements=["cik"], index_where=sa.text("cik IS NOT NULL")
                )
                .returning(Company.id)
            )
            result = self._session.execute(statement)
            inserted_id = result.scalar_one_or_none()
            if inserted_id is None:
                # Lost the race: a concurrent resolution of the same CIK won.
                winner = self._session.scalar(
                    sa.select(Company).where(Company.cik == reference.cik)
                )
                if winner is None:  # pragma: no cover - unreachable under READ COMMITTED
                    message = (
                        f"ON CONFLICT reported cik {reference.cik!r} as held, but no "
                        "committed row was visible"
                    )
                    raise RuntimeError(message)
                return winner, False, f"cik:{reference.cik}"

            company = self._session.get(Company, inserted_id)
            assert company is not None
            return company, True, f"cik:{reference.cik}"

        # No CIK. A FIGI identifies an *instrument*, not an issuer, so it can
        # reach an issuer only by way of a security we already hold. If we hold
        # one, the issuer is known with certainty; if not, there is nothing to
        # match on and creating a company would risk a duplicate.
        if reference.share_class_figi is not None:
            owner = self._company_for_figi(reference.share_class_figi)
            if owner is not None:
                return owner, False, f"figi:{reference.share_class_figi}"

        # A composite FIGI cannot rescue this case, and that is a schema fact
        # rather than an oversight. It is *issuer*-scoped -- every share class of
        # an issuer shares it -- while `security_identifier` is unique on
        # (type, value) globally. Recording it per security would therefore
        # succeed for the first share class and fail on the second with a
        # constraint error, so it is not recorded there at all. Giving it a home
        # needs a company-level identifier table, which is a later migration; the
        # value is carried on the canonical DTO meanwhile so nothing is lost at the
        # adapter boundary. Until then, an instrument known only by its composite
        # FIGI is refused rather than guessed at.
        message = (
            f"{reference.ticker}: no CIK and no previously-stored share-class "
            "FIGI, so there is no stable identifier to match an issuer on. "
            "Creating a row here would risk a second identity for an issuer we "
            "may already hold under a different name; matching on the name would "
            "risk merging two distinct issuers. Refusing instead."
        )
        raise UnresolvableReferenceError(message)

    def _company_for_figi(self, figi: str) -> Company | None:
        """Return the issuer reachable from a stored FIGI identifier, if any."""
        return self._session.scalar(
            sa.select(Company)
            .join(Security, Security.company_id == Company.id)
            .join(SecurityIdentifier, SecurityIdentifier.security_id == Security.id)
            .where(
                SecurityIdentifier.identifier_type.in_(_FIGI_TYPES),
                SecurityIdentifier.identifier_value == figi,
            )
        )

    # ── Security ───────────────────────────────────────────────────────────

    def _resolve_security(
        self,
        reference: CanonicalEquityReference,
        *,
        company_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> tuple[Security, bool]:
        """Find or create the instrument.

        Identity comes from the share-class FIGI when there is one, because a
        FIGI is assigned to a specific instrument and survives the company
        changing its ticker. Without a FIGI the identity falls back to
        ``(company, label)``, which is weaker and documented as such: a company
        that changes ticker *and* has no FIGI will get a second security row.
        That is a duplicate, which is visible and recoverable -- strictly better
        than the alternative of guessing and merging.
        """
        figi = reference.share_class_figi
        if figi is not None:
            # A FIGI names one specific instrument. If we hold it, that is the
            # instrument -- full stop, and the name is not consulted.
            #
            # If we do *not* hold it, a new instrument must be created. Falling
            # back to a name match here would be a merge: a second share class of
            # the same company trading under the same ticker on the same venue
            # carries the same synthesised name as the first, so the fallback
            # would attach a distinct instrument's prices to the existing one.
            # That failure is invisible downstream, which is exactly why the
            # identity rule is enforced rather than merely preferred.
            found = self._security_for_figi(figi)
            if found is not None:
                return found, False
            return self._create_security(
                reference,
                company_id=company_id,
                source_id=source_id,
                # Never reuse a name match here: the FIGI already decided this
                # is a different instrument.
                allow_name_reuse=False,
            )

        # No FIGI: the instrument's identity is (company, synthesised name). Weaker
        # by construction, and a ticker change without a FIGI yields a second
        # security for the same company. That is a duplicate, which is visible and
        # recoverable -- strictly better than guessing and merging.
        return self._create_security(
            reference,
            company_id=company_id,
            source_id=source_id,
            # Here the name *is* the identity, so a match is the same
            # instrument and is reused.
            allow_name_reuse=True,
        )

    def _security_type(self, reference: CanonicalEquityReference) -> SecurityType:
        """Translate the canonical instrument class to the storage enum."""
        security_type = _SECURITY_TYPE_ENUM.get(reference.security_type)
        if security_type is None:
            message = (
                f"{reference.ticker}: canonical security type "
                f"{reference.security_type!r} has no counterpart in the storage "
                "vocabulary"
            )
            raise UnresolvableReferenceError(message)
        return security_type

    def _create_security(
        self,
        reference: CanonicalEquityReference,
        *,
        company_id: uuid.UUID,
        source_id: uuid.UUID,
        allow_name_reuse: bool,
    ) -> tuple[Security, bool]:
        """Create the instrument row, optionally reusing an existing name match.

        Returns ``(security, created)``. ``created`` is ``False`` whenever an
        existing row was reused -- by the name lookup below, or by any of the
        reuse paths inside :meth:`_insert_security` -- and callers must
        propagate it rather than assume "this method was called" means
        "something was written".

        ``allow_name_reuse`` encodes which of the two identity rules applies:

        * ``True`` -- no FIGI, so ``(company_id, name)`` *is* the identity. An
          existing row with that name is the same instrument and is returned.
        * ``False`` -- a FIGI is present and did not match, so this is known to be
          a different instrument that happens to share a display name. Reusing the
          name match would merge them.

        ``(company_id, name)`` is unique in the database, so a name collision is a
        hard constraint rather than a style preference. When the natural name is
        taken by a different instrument, a short FIGI-derived suffix is appended.
        It is derived from the FIGI rather than a counter so it is stable across
        re-runs: the same reference always yields the same name, which keeps the
        table readable and reproducible.

        Concurrency
        -----------
        This instrument's true identity, on the FIGI path, lives partly on a
        *different* table: ``uq_security_identifier_type_value`` is what
        actually says "this FIGI already names a security", and
        ``Security.id`` does not exist until after the row naming it is
        inserted. A single ``ON CONFLICT`` clause on ``security`` cannot
        arbitrate that. So the insert of the ``Security`` row and (on the FIGI
        path) its ``SecurityIdentifier`` are wrapped in one ``SAVEPOINT``: if
        either unique index is violated by a concurrent writer, only that
        savepoint rolls back -- not the whole ingestion batch -- and the
        method re-resolves by reading whichever row the winner created.
        """
        label = _SECURITY_TYPE_LABEL.get(reference.security_type, reference.security_type)
        name = f"{reference.ticker} {label}"
        figi = reference.share_class_figi
        taken = self._select_security_by_name(company_id, name)
        if taken is not None:
            if allow_name_reuse:
                return taken, False
            # A FIGI is present on this path by construction, so the suffix is
            # available; the assert documents that rather than handling None.
            assert figi is not None
            name = f"{name} [{figi[-6:]}]"
        return self._insert_security(
            reference,
            company_id=company_id,
            source_id=source_id,
            name=name,
            allow_name_reuse=allow_name_reuse,
        )

    def _select_security_by_name(self, company_id: uuid.UUID, name: str) -> Security | None:
        return self._session.scalar(
            sa.select(Security).where(Security.company_id == company_id, Security.name == name)
        )

    def _insert_security(
        self,
        reference: CanonicalEquityReference,
        *,
        company_id: uuid.UUID,
        source_id: uuid.UUID,
        name: str,
        allow_name_reuse: bool,
    ) -> tuple[Security, bool]:
        """Atomically insert one ``(Security, SecurityIdentifier?)`` pair.

        Returns ``(security, created)``, exactly like :meth:`_create_security`.

        See :meth:`_create_security` for why both live in one ``SAVEPOINT``.
        On any unique-index violation the savepoint is rolled back and the
        method re-resolves: a FIGI collision means a concurrent writer already
        holds the exact instrument this call wanted, so that row is returned
        (``created=False``); a name collision on the FIGI path means a
        *different* concurrent instrument took this name first, so the call
        retries once with the FIGI suffix, exactly as the sequential code path
        would have chosen up front had it seen the collision before inserting.
        """
        figi = reference.share_class_figi
        security_id = uuid.uuid4()
        try:
            with self._session.begin_nested():
                statement = (
                    postgresql.insert(Security)
                    .values(
                        id=security_id,
                        company_id=company_id,
                        security_type=self._security_type(reference),
                        name=name,
                        # The trading currency is the instrument's currency.
                        # Taken from the reference when supplied; a US equity
                        # venue that omits it is USD by venue convention, and
                        # every mapped venue in this milestone settles in USD.
                        currency=reference.currency_code or "USD",
                        is_active=(
                            bool(reference.is_active) if reference.is_active is not None else True
                        ),
                    )
                    .on_conflict_do_nothing(index_elements=["company_id", "name"])
                    .returning(Security.id)
                )
                result = self._session.execute(statement)
                inserted_id = result.scalar_one_or_none()
                if inserted_id is not None and figi is not None:
                    self._session.add(
                        SecurityIdentifier(
                            security_id=inserted_id,
                            identifier_type=IdentifierType.FIGI_SHARE_CLASS,
                            identifier_value=figi,
                            source_id=source_id,
                        )
                    )
                    # Flushed inside the savepoint so a concurrently-claimed
                    # FIGI raises here, while it can still be rolled back to
                    # just this savepoint rather than the whole batch.
                    self._session.flush()
        except IntegrityError:
            if figi is not None:
                owner = self._security_for_figi(figi)
                if owner is not None:
                    return owner, False
            existing = self._select_security_by_name(company_id, name)
            if existing is not None:
                if allow_name_reuse:
                    return existing, False
                if figi is not None and not name.endswith("]"):
                    suffixed = f"{name} [{figi[-6:]}]"
                    return self._insert_security(
                        reference,
                        company_id=company_id,
                        source_id=source_id,
                        name=suffixed,
                        allow_name_reuse=allow_name_reuse,
                    )
            raise
        else:
            if inserted_id is None:
                # Lost the (company_id, name) race with no FIGI collision
                # involved. Re-select and apply the same reuse rule.
                existing = self._select_security_by_name(company_id, name)
                if existing is not None:
                    if allow_name_reuse:
                        return existing, False
                    if figi is not None and not name.endswith("]"):
                        suffixed = f"{name} [{figi[-6:]}]"
                        return self._insert_security(
                            reference,
                            company_id=company_id,
                            source_id=source_id,
                            name=suffixed,
                            allow_name_reuse=allow_name_reuse,
                        )
                message = (  # pragma: no cover - unreachable under READ COMMITTED
                    f"ON CONFLICT reported ({company_id}, {name!r}) as held, but no "
                    "committed row was visible"
                )
                raise RuntimeError(message)  # pragma: no cover

        security = self._session.get(Security, inserted_id)
        assert security is not None
        return security, True

    def _security_for_figi(self, figi: str) -> Security | None:
        return self._session.scalar(
            sa.select(Security)
            .join(SecurityIdentifier, SecurityIdentifier.security_id == Security.id)
            .where(
                SecurityIdentifier.identifier_type.in_(_FIGI_TYPES),
                SecurityIdentifier.identifier_value == figi,
            )
        )

    # ── Listing ────────────────────────────────────────────────────────────

    def _resolve_listing(
        self,
        reference: CanonicalEquityReference,
        *,
        security_id: uuid.UUID,
        exchange_id: uuid.UUID,
        as_of: date,
    ) -> tuple[Listing, bool]:
        """Find or create the current listing of ``ticker`` on this venue.

        Matches the *current* listing only (``valid_to IS NULL``), because a
        reference payload describes the present. A historical interval matching
        the ticker is the past, and reusing it would retroactively extend a
        listing we know ended.

        Concurrency
        -----------
        The creation branch inserts through ``ON CONFLICT DO NOTHING`` against
        ``uq_listing_current_exchange_ticker`` -- the same
        ``(exchange_id, ticker) WHERE valid_to IS NULL`` identity checked
        above -- so two concurrent resolutions of the same ticker on the same
        venue converge on one row instead of the loser raising a raw
        ``IntegrityError``. That conflict target does not cover
        ``uq_listing_current_primary_security``: two *different* new listings
        for the same security on two different venues can still both compute
        "I am the first" and race to claim ``is_primary``. That is a real but
        separate race -- it is about which listing wins a presentational tie,
        not about creating a duplicate canonical identity -- so it is handled
        with one bounded retry inside a ``SAVEPOINT``: if the primary claim
        loses, the insert is retried once, non-primary, deferring to whichever
        writer won.
        """
        existing = self._current_listing(exchange_id, reference.ticker)
        if existing is not None:
            if existing.security_id != security_id:
                exchange = self._session.get(Exchange, exchange_id)
                mic = exchange.mic if exchange is not None else "?"
                raise TickerReuseConflictError(
                    ticker=reference.ticker,
                    mic=mic,
                    existing_security_id=existing.security_id,
                    incoming_security_id=security_id,
                )
            return existing, False

        is_primary = self._is_first_listing_for(security_id)
        inserted_id = self._insert_listing(
            reference,
            security_id=security_id,
            exchange_id=exchange_id,
            as_of=as_of,
            is_primary=is_primary,
        )
        if inserted_id is None:
            # Lost the (exchange_id, ticker) race: apply the same reuse check
            # as above against the row the winner created.
            existing = self._current_listing(exchange_id, reference.ticker)
            if existing is None:  # pragma: no cover - unreachable under READ COMMITTED
                message = (
                    f"ON CONFLICT reported ({exchange_id}, {reference.ticker!r}) as held, "
                    "but no committed row was visible"
                )
                raise RuntimeError(message)
            if existing.security_id != security_id:
                exchange = self._session.get(Exchange, exchange_id)
                mic = exchange.mic if exchange is not None else "?"
                raise TickerReuseConflictError(
                    ticker=reference.ticker,
                    mic=mic,
                    existing_security_id=existing.security_id,
                    incoming_security_id=security_id,
                )
            return existing, False

        listing = self._session.get(Listing, inserted_id)
        assert listing is not None
        return listing, True

    def _current_listing(self, exchange_id: uuid.UUID, ticker: str) -> Listing | None:
        return self._session.scalar(
            sa.select(Listing).where(
                Listing.exchange_id == exchange_id,
                Listing.ticker == ticker,
                Listing.valid_to.is_(None),
            )
        )

    def _insert_listing(
        self,
        reference: CanonicalEquityReference,
        *,
        security_id: uuid.UUID,
        exchange_id: uuid.UUID,
        as_of: date,
        is_primary: bool,
    ) -> uuid.UUID | None:
        """One atomic insert attempt; ``None`` means the ticker/venue was held.

        Retries once, non-primary, if the *only* other constraint on this row
        (``uq_listing_current_primary_security``) is what raised -- see the
        concurrency note on :meth:`_resolve_listing`.
        """
        listing_id = uuid.uuid4()

        def _attempt(*, primary: bool) -> uuid.UUID | None:
            statement = (
                postgresql.insert(Listing)
                .values(
                    id=listing_id,
                    security_id=security_id,
                    exchange_id=exchange_id,
                    ticker=reference.ticker,
                    # First observed, not first listed. See the module docstring.
                    valid_from=as_of,
                    valid_to=None,
                    is_primary=primary,
                )
                .on_conflict_do_nothing(
                    index_elements=["exchange_id", "ticker"],
                    index_where=sa.text("valid_to IS NULL"),
                )
                .returning(Listing.id)
            )
            result = self._session.execute(statement)
            return result.scalar_one_or_none()

        try:
            with self._session.begin_nested():
                return _attempt(primary=is_primary)
        except IntegrityError:
            if not is_primary:  # pragma: no cover - defensive; only reachable via the primary index
                raise
            with self._session.begin_nested():
                return _attempt(primary=False)

    def _is_first_listing_for(self, security_id: uuid.UUID) -> bool:
        """Whether this would be the security's first current listing.

        A security traded on two venues has two current listings, and the schema
        allows exactly one of them to be primary -- ``uq_listing_current_primary_security``
        is unique on ``(security_id) WHERE is_primary AND valid_to IS NULL``.
        Marking both primary would therefore fail on the second venue with a
        constraint error that says nothing about the actual mistake.

        The first one observed is the primary: it is the venue this integration
        first saw the instrument on, which is the only ordering the data actually
        supports. There is no basis here for preferring a primary exchange, and a
        provider's "primary" field is exactly the sort of claim that changes
        without notice.
        """
        already = self._session.scalar(
            sa.select(sa.func.count())
            .select_from(Listing)
            .where(Listing.security_id == security_id, Listing.valid_to.is_(None))
        )
        return not already
