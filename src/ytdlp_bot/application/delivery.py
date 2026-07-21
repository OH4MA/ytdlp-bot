"""Artifact delivery: direct upload or signed-link fallback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ytdlp_bot.domain.enums import DeliveryPlan, FailureCode, UploadOutcome
from ytdlp_bot.domain.identity import JobId, MessageContext, MessageReference
from ytdlp_bot.domain.jobs import Artifact
from ytdlp_bot.domain.progress import (
    ArtifactDescriptor,
    DeliveryPlanDecision,
    DeliveryResult,
    FinalOutcomeView,
)


class PlatformDelivery(Protocol):
    async def upload_artifact(
        self, context: MessageContext, descriptor: ArtifactDescriptor
    ) -> UploadOutcome: ...

    async def send_final(
        self, message_reference: MessageReference, view: FinalOutcomeView
    ) -> None: ...


class LinkIssuer(Protocol):
    def issue(
        self,
        *,
        artifact_id: str,
        display_name: str,
        token_version: int,
        now: datetime,
        link_lifetime_seconds: int,
        artifact_expires_at: datetime,
        job_id: str | None = None,
    ) -> object: ...


class ArtifactPathResolver(Protocol):
    def resolve_artifact_path(self, storage_key: str) -> str: ...


@dataclass
class DeliveryService:
    platform: PlatformDelivery
    link_issuer: LinkIssuer
    link_lifetime_seconds: int
    path_resolver: ArtifactPathResolver | None = None

    def plan_for(self, artifact: Artifact, limit_bytes: int) -> DeliveryPlanDecision:
        return DeliveryPlanDecision.decide(artifact.byte_size, limit_bytes)

    def _local_path(self, storage_key: str) -> str | None:
        if self.path_resolver is None:
            return None
        try:
            return self.path_resolver.resolve_artifact_path(storage_key)
        except Exception:
            return None

    def _issued_url(self, link: object) -> str | None:
        url = getattr(link, "url", None)
        return url if isinstance(url, str) and url else None

    async def deliver(
        self,
        *,
        job_id: JobId,
        artifact: Artifact,
        context: MessageContext,
        message_reference: MessageReference,
        now: datetime,
        partial: bool = False,
    ) -> DeliveryResult:
        decision = self.plan_for(artifact, context.effective_upload_limit_bytes)
        if decision.plan is DeliveryPlan.DIRECT_UPLOAD:
            local_path = self._local_path(artifact.storage_key)
            outcome = await self.platform.upload_artifact(
                context,
                ArtifactDescriptor(
                    artifact_id=artifact.artifact_id.value,
                    display_name=artifact.display_name,
                    media_type=artifact.media_type.value,
                    byte_size=artifact.byte_size,
                    storage_key=artifact.storage_key,
                    local_path=local_path,
                ),
            )
            if outcome is UploadOutcome.UPLOADED:
                view = FinalOutcomeView(
                    job_id=job_id,
                    outcome="completed_with_errors" if partial else "completed",
                    message_key=(
                        "outcome.completed_with_errors" if partial else "outcome.completed"
                    ),
                    delivery_plan=DeliveryPlan.DIRECT_UPLOAD,
                )
                await self.platform.send_final(message_reference, view)
                return DeliveryResult(
                    plan=DeliveryPlan.DIRECT_UPLOAD,
                    attempt_count=1,
                    upload_outcome=outcome,
                    platform_message=message_reference,
                )
            if outcome is not UploadOutcome.TOO_LARGE and outcome in {
                UploadOutcome.TEMPORARILY_UNAVAILABLE,
                UploadOutcome.RATE_LIMITED,
            }:
                return DeliveryResult(
                    plan=DeliveryPlan.DIRECT_UPLOAD,
                    attempt_count=1,
                    upload_outcome=outcome,
                    error_code=FailureCode.PLATFORM_UNAVAILABLE,
                )

        # Signed link path (too_large or planned signed_link).
        link = self.link_issuer.issue(
            artifact_id=artifact.artifact_id.value,
            display_name=artifact.display_name,
            token_version=artifact.token_version,
            now=now,
            link_lifetime_seconds=self.link_lifetime_seconds,
            artifact_expires_at=artifact.expires_at,
            job_id=job_id.value,
        )
        download_url = self._issued_url(link)
        view = FinalOutcomeView(
            job_id=job_id,
            outcome="completed_with_errors" if partial else "completed",
            message_key=("outcome.completed_with_errors" if partial else "outcome.completed"),
            has_signed_link_hint=True,
            delivery_plan=DeliveryPlan.SIGNED_LINK,
            download_url=download_url,
        )
        await self.platform.send_final(message_reference, view)
        expires = getattr(link, "expires_at", None)
        return DeliveryResult(
            plan=DeliveryPlan.SIGNED_LINK,
            attempt_count=1,
            upload_outcome=UploadOutcome.TOO_LARGE
            if decision.plan is DeliveryPlan.DIRECT_UPLOAD
            else None,
            link_expires_at=expires,
        )
