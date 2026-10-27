"""Onboarding a venue: place it on a stamp, record it in the catalog, create it in the stamp.

Two databases and no distributed transaction, so the job is the source of truth (ADR-0017):
1. catalog transaction: place, record tenant (if new), venue (`provisioning`), entitlements, job;
2. create the tenant (if new) and venue in the stamp, idempotently;
3. catalog transaction: job `succeeded`, venue and tenant `active` (routable).
Step 2 retries transient failures with backoff; if it still fails the job is `failed` with an
error code and the venue doesn't route, and `retry` runs steps 2-3 again. A job stuck in `running`
(process died) can be retried once stale (database clock); a late finisher can't overwrite a newer
run's outcome. An Idempotency-Key makes step 1 safe to replay. The job row is the state a durable
orchestration (agent runtime, P0-08) will drive, so the two don't fork.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from va_control_plane import catalog
from va_control_plane.errors import OnboardingConflictError, PlacementError
from va_control_plane.placement import choose_stamp
from va_control_plane.provisioning import ProvisionRequest, StampConnection, StampProvisioner
from va_core.errors import NotFoundError, VenueAgentsError
from va_core.http import RetryPolicy
from va_core.telemetry import get_logger
from va_core.tenancy import TenantContext, tenant_scope
from va_db.catalog.models import (
    DirectoryStatus,
    Entitlement,
    JobStatus,
    OnboardingJob,
    Ring,
    Stamp,
    TenantDirectory,
    VenueDirectory,
)
from va_db.ids import uuid7

logger = get_logger(__name__)

STALE_RUNNING_JOB = timedelta(minutes=5)
PROVISION_RETRY = RetryPolicy(max_attempts=3, base_delay=0.5, max_delay=5.0)


@dataclass(frozen=True)
class VenueOnboarding:
    """What an operator asked for. Exactly one of `tenant_id` / `tenant_name`."""

    venue_name: str
    timezone: str
    region: str
    plan: str
    ring: Ring = Ring.GENERAL
    tenant_id: UUID | None = None
    tenant_name: str | None = None
    stamp_id: str | None = None
    entitlements: frozenset[str] = frozenset()
    idempotency_key: str | None = None


@dataclass(frozen=True)
class OnboardingResult:
    tenant_id: UUID
    venue_id: UUID
    stamp_id: str
    job_id: UUID
    status: JobStatus
    error_code: str | None


def _result(job: OnboardingJob, stamp_id: str) -> OnboardingResult:
    return OnboardingResult(
        tenant_id=job.tenant_id,
        venue_id=job.venue_id,
        stamp_id=stamp_id,
        job_id=job.id,
        status=JobStatus(job.status),
        error_code=job.error_code,
    )


def _must[T](row: T | None, what: str) -> T:
    """Rows the catalog's foreign keys guarantee; missing means the catalog is inconsistent."""
    if row is None:
        raise NotFoundError(f"onboarding {what} not found")
    return row


def _error_code(exc: BaseException) -> str:
    # Only our own codes: driver and SQLAlchemy errors have `.code` attributes of their own.
    return exc.code if isinstance(exc, VenueAgentsError) else "stamp_unavailable"


def _transient(exc: BaseException) -> bool:
    """Connection-level failures are worth retrying; our typed errors (config, auth) are not."""
    return not isinstance(exc, VenueAgentsError)


class Onboarding:
    def __init__(
        self,
        catalog_sessions: async_sessionmaker[AsyncSession],
        provisioner: StampProvisioner,
        *,
        retry: RetryPolicy = PROVISION_RETRY,
    ) -> None:
        self._catalog = catalog_sessions
        self._provisioner = provisioner
        self._retry = retry

    async def onboard(self, request: VenueOnboarding) -> OnboardingResult:
        if (request.tenant_id is None) == (request.tenant_name is None):
            raise ValueError("give exactly one of tenant_id and tenant_name")
        if request.idempotency_key is not None:
            replay = await self._by_key(request.idempotency_key)
            if replay is not None:
                return replay
        try:
            tenant_id, job_id = await self._place(request)
        except IntegrityError:
            # A concurrent request with the same key won the insert: return its job.
            if request.idempotency_key is not None:
                replay = await self._by_key(request.idempotency_key)
                if replay is not None:
                    return replay
            raise
        return await self._run(tenant_id, job_id)

    async def retry(self, venue_id: UUID) -> OnboardingResult:
        async with self._catalog() as session:
            venue = (
                await session.execute(
                    select(VenueDirectory).where(VenueDirectory.venue_id == venue_id)
                )
            ).scalar_one_or_none()
            if venue is None:
                raise NotFoundError("no onboarding job for this venue")
            job = (
                await session.execute(
                    select(OnboardingJob)
                    .where(
                        OnboardingJob.tenant_id == venue.tenant_id,
                        OnboardingJob.venue_id == venue_id,
                    )
                    .order_by(OnboardingJob.created_at.desc(), OnboardingJob.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if job is None:
            raise NotFoundError("no onboarding job for this venue")
        return await self._run(job.tenant_id, job.id)

    async def _by_key(self, key: str) -> OnboardingResult | None:
        async with self._catalog() as session:
            job = (
                await session.execute(
                    select(OnboardingJob).where(OnboardingJob.idempotency_key == key)
                )
            ).scalar_one_or_none()
            if job is None:
                return None
            venue = _must(await session.get(VenueDirectory, (job.tenant_id, job.venue_id)), "venue")
            return _result(job, venue.stamp_id)

    async def _place(self, request: VenueOnboarding) -> tuple[UUID, UUID]:
        async with self._catalog.begin() as session:
            await catalog.lock_placement(session)
            tenant_stamp: str | None = None
            if request.tenant_id is not None:
                tenant = await session.get(TenantDirectory, request.tenant_id)
                if tenant is None:
                    raise NotFoundError("tenant not found")
                if tenant.status != DirectoryStatus.ACTIVE:
                    raise PlacementError(f"tenant is {tenant.status}; venues can't be added")
                tenant_stamp = tenant.stamp_id
            stamp = choose_stamp(
                await catalog.stamp_loads(session),
                region=request.region,
                ring=request.ring,
                tenant_stamp=tenant_stamp,
                requested=request.stamp_id,
            )
            # New tenant IDs are minted here, never taken from the request.
            tenant_id = request.tenant_id or uuid7()
            venue_id = uuid7()
            if request.tenant_id is None:
                session.add(TenantDirectory(tenant_id=tenant_id, stamp_id=stamp.id))
                await session.flush()
            session.add(
                VenueDirectory(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    stamp_id=stamp.id,
                    region=request.region,
                    plan=request.plan,
                )
            )
            await session.flush()
            session.add_all(
                Entitlement(tenant_id=tenant_id, venue_id=venue_id, entitlement=name)
                for name in sorted(request.entitlements)
            )
            job = OnboardingJob(
                tenant_id=tenant_id,
                venue_id=venue_id,
                tenant_name=request.tenant_name,
                venue_name=request.venue_name,
                timezone=request.timezone,
                idempotency_key=request.idempotency_key,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        with tenant_scope(TenantContext(tenant_id=tenant_id, venue_id=venue_id, stamp_id=stamp.id)):
            logger.info(
                "venue placed", extra={"job_id": str(job_id), "new_tenant": tenant_stamp is None}
            )
        return tenant_id, job_id

    async def _run(self, tenant_id: UUID, job_id: UUID) -> OnboardingResult:
        async with self._catalog.begin() as session:
            job = await session.get(OnboardingJob, (tenant_id, job_id), with_for_update=True)
            if job is None:
                raise NotFoundError("onboarding job not found")
            venue = _must(await session.get(VenueDirectory, (tenant_id, job.venue_id)), "venue")
            if job.status == JobStatus.SUCCEEDED:
                return _result(job, venue.stamp_id)
            if job.status == JobStatus.RUNNING and not await _is_stale(session, job):
                raise OnboardingConflictError("onboarding is already running for this venue")
            stamp = _must(await session.get(Stamp, venue.stamp_id), "stamp")
            connection = StampConnection.from_stamp(stamp)
            job.status = JobStatus.RUNNING
            job.attempts += 1
            job.error_code = None
            attempt = job.attempts
            provision = ProvisionRequest(
                tenant_id=tenant_id,
                venue_id=job.venue_id,
                tenant_name=job.tenant_name,
                venue_name=job.venue_name,
                timezone=job.timezone,
            )
        # The one place a TenantContext comes from catalog state: a tenant this service minted or
        # validated above, used only with va_provision rights (ADR-0017).
        ctx = TenantContext(tenant_id=tenant_id, venue_id=provision.venue_id, stamp_id=stamp.id)
        with tenant_scope(ctx):
            try:
                await self._provision(connection, provision)
            except Exception as exc:
                code = _error_code(exc)
                logger.warning(
                    "stamp provisioning failed",
                    extra={
                        "job_id": str(job_id),
                        "error_code": code,
                        "error_type": type(exc).__name__,
                    },
                )
                return await self._finish(tenant_id, job_id, attempt, JobStatus.FAILED, code)
            result = await self._finish(tenant_id, job_id, attempt, JobStatus.SUCCEEDED, None)
            logger.info("venue onboarded", extra={"job_id": str(job_id)})
            return result

    async def _provision(self, connection: StampConnection, request: ProvisionRequest) -> None:
        """Retry transient failures with full-jitter backoff (provisioning is idempotent)."""
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                await self._provisioner.provision(connection, request)
                return
            except Exception as exc:
                if attempt == self._retry.max_attempts or not _transient(exc):
                    raise
                await asyncio.sleep(self._retry.backoff(attempt, random.random))

    async def _finish(
        self,
        tenant_id: UUID,
        job_id: UUID,
        attempt: int,
        status: JobStatus,
        error_code: str | None,
    ) -> OnboardingResult:
        async with self._catalog.begin() as session:
            job = _must(
                await session.get(OnboardingJob, (tenant_id, job_id), with_for_update=True), "job"
            )
            venue = _must(await session.get(VenueDirectory, (tenant_id, job.venue_id)), "venue")
            if job.status != JobStatus.RUNNING or job.attempts != attempt:
                # A newer run (stale-job retry) owns the job now: keep its outcome.
                return _result(job, venue.stamp_id)
            job.status = status
            job.error_code = error_code
            if status == JobStatus.SUCCEEDED:
                if venue.status == DirectoryStatus.PROVISIONING:
                    venue.status = DirectoryStatus.ACTIVE
                tenant = await session.get(TenantDirectory, tenant_id)
                if tenant is not None and tenant.status == DirectoryStatus.PROVISIONING:
                    tenant.status = DirectoryStatus.ACTIVE
            await session.flush()
            return _result(job, venue.stamp_id)


async def _is_stale(session: AsyncSession, job: OnboardingJob) -> bool:
    """Compared on the database clock, not this process's."""
    stale = await session.scalar(
        select(OnboardingJob.updated_at < func.now() - STALE_RUNNING_JOB).where(
            OnboardingJob.tenant_id == job.tenant_id, OnboardingJob.id == job.id
        )
    )
    return bool(stale)
