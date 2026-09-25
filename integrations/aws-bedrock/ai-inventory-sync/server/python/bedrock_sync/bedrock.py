"""Read the Bedrock model inventory via boto3 and flatten it to ``BedrockModel``.

Three listings, one ``bedrock`` control-plane client (not ``bedrock-runtime``,
which only invokes models and has no listing operations):

- ``list_foundation_models`` — the region-wide catalog of base models.
- ``list_custom_models`` — this account's fine-tuned / distilled models.
- ``list_imported_models`` — this account's Custom Model Import models.

Shapes and pagination below were read out of botocore's own service model
(``botocore/data/bedrock/2023-04-20/service-2.json``) rather than from the
docs, so they match what the SDK will actually send and accept.

Each listing is fetched independently and a failure in one is returned as an
error string rather than raised: an IAM policy that grants
``bedrock:ListFoundationModels`` but not ``bedrock:ListCustomModels`` is a
normal state of the world, and it should degrade to a partial sync, not to no
sync at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger("bedrock_sync.bedrock")

# ClientError is NOT a subclass of BotoCoreError — catching only one of them
# misses half the failure modes: BotoCoreError alone skips every API-side
# failure (access denied, throttling, validation), which is most of them.
AWS_ERRORS = (BotoCoreError, ClientError)


@dataclass(frozen=True)
class BedrockModel:
    """One Bedrock model, flattened to exactly what the Credo AI Model
    resource can hold.

    The Credo AI Model has no metadata/version/tags fields — only ``name``,
    ``summary``, ``source`` (see sync.py) — so everything else Bedrock knows
    about a model is rendered into ``summary`` as text. ``kind`` is for
    logging and counters, not for the API payload.
    """

    name: str
    summary: str
    kind: str  # "foundation" | "custom" | "imported"
    provider: str | None = None


@dataclass
class Inventory:
    models: list[BedrockModel]
    errors: list[str]
    inference_profiles: dict[str, str] = field(default_factory=dict)


PROFILE_PREFIXES = ("global.", "us.", "eu.", "apac.")


def resolve_model_key(
    model_id: str, *, names: set[str], profiles: dict[str, str]
) -> str | None:
    """The registry name for what a harness says it calls, or ``None``.

    A harness names an *inference profile* more often than a model — AWS
    recommends profiles for cross-region capacity — and the model registry only
    ever holds model ids, so the harness's own string frequently misses.

    Shared by two callers: the catalog filter below, which needs to know which
    foundation models are referenced, and ``sync.link_use_case_models``, which
    needs the record to link to. One function so the two can never disagree.
    """
    if model_id in names:
        return model_id

    mapped = profiles.get(model_id)
    if mapped and mapped in names:
        return mapped

    for prefix in PROFILE_PREFIXES:
        if model_id.startswith(prefix):
            stripped = model_id[len(prefix) :]
            if stripped in names:
                return stripped
    return None


def _joined(values: list[str] | None) -> str:
    # An empty list is information ("supports no customizations") and must not
    # read as "unknown" — only an absent key is genuinely unknown.
    if values is None:
        return "unknown"
    return ", ".join(values) if values else "none"


def _day(value: datetime | None) -> str:
    # boto3 parses AWS timestamps into datetimes; be tolerant of a stubbed or
    # future shape that hands back something else.
    return value.date().isoformat() if isinstance(value, datetime) else "unknown"


class BedrockReader:
    """Wraps the ``bedrock`` control-plane client.

    Credentials come from boto3's default chain — profile, environment,
    instance/task role — so this class takes a region and nothing else. That is
    the "simplest approach" the integration is specified around: no AWS secret
    is ever read, stored or logged by this codebase.
    """

    def __init__(self, region: str = "", client=None):
        self._region = region
        # `client` exists so a caller can supply its own — a pre-configured
        # session, a different endpoint, or a stub in a test. The CLI never
        # passes one: it lets boto3 build the client from the default
        # credential chain, which is the whole point of holding no AWS keys.
        self._client = client or boto3.client("bedrock", region_name=region or None)

    @property
    def region(self) -> str:
        return self._region or self._client.meta.region_name or "unknown"

    # -- listings --------------------------------------------------------------

    def list_foundation_models(self) -> list[BedrockModel]:
        # No paginator and no nextToken on this operation (confirmed against
        # botocore's paginators-1.json and the service model) — one call
        # returns the whole regional catalog.
        summaries = self._client.list_foundation_models().get("modelSummaries", [])
        return [self._foundation(s) for s in summaries]

    def list_custom_models(self) -> list[BedrockModel]:
        return [
            self._custom(s)
            for page in self._client.get_paginator("list_custom_models").paginate()
            for s in page.get("modelSummaries", [])
        ]

    def list_imported_models(self) -> list[BedrockModel]:
        return [
            self._imported(s)
            for page in self._client.get_paginator("list_imported_models").paginate()
            for s in page.get("modelSummaries", [])
        ]

    def list_inference_profiles(self) -> dict[str, str]:
        profiles: dict[str, str] = {}
        for summary in (
            item
            for page in self._client.get_paginator("list_inference_profiles").paginate()
            for item in page.get("inferenceProfileSummaries", [])
        ):
            # The same model repeated once per routed region is the normal
            # shape, so compare the distinct set rather than the list length.
            model_ids = {
                m["modelArn"].rsplit("/", 1)[-1]
                for m in summary.get("models", [])
                if m.get("modelArn")
            }
            if len(model_ids) == 1:
                profiles[summary["inferenceProfileId"]] = model_ids.pop()
            elif model_ids:
                logger.info(
                    "Inference profile %s routes to %d distinct models — "
                    "not usable as a link target",
                    summary["inferenceProfileId"],
                    len(model_ids),
                )
        return profiles

    def inventory(
        self,
        *,
        referenced_model_ids: set[str] | None = None,
        include_inference_profiles: bool = False,
    ) -> Inventory:
        listings: list[tuple[str, callable]] = [
            ("foundation models", self.list_foundation_models),
            ("custom models", self.list_custom_models),
            ("imported models", self.list_imported_models),
        ]

        models: list[BedrockModel] = []
        errors: list[str] = []
        for label, fetch in listings:
            try:
                found = fetch()
            except AWS_ERRORS as exc:
                logger.warning("Failed to list %s: %s", label, exc)
                errors.append(f"list {label}: {exc}")
                continue
            logger.info("Found %d %s in %s", len(found), label, self.region)
            models.extend(found)

        profiles: dict[str, str] = {}
        if include_inference_profiles:
            try:
                profiles = self.list_inference_profiles()
            except AWS_ERRORS as exc:
                logger.warning("Failed to list inference profiles: %s", exc)
                errors.append(f"list inference profiles: {exc}")
            else:
                logger.info(
                    "Found %d inference profiles in %s", len(profiles), self.region
                )

        if referenced_model_ids is not None:
            models = self._referenced_only(models, referenced_model_ids, profiles)

        return Inventory(models=models, errors=errors, inference_profiles=profiles)

    def _referenced_only(
        self,
        models: list[BedrockModel],
        referenced: set[str],
        profiles: dict[str, str],
    ) -> list[BedrockModel]:
        """Drop foundation models no harness names.

        Resolution runs through the profile map because a harness almost always
        names a profile rather than a model id — filtering on the raw strings
        would match nothing and empty the catalog.
        """
        names = {m.name for m in models}
        keep = {
            key
            for key in (
                resolve_model_key(rid, names=names, profiles=profiles)
                for rid in referenced
            )
            if key
        }
        kept = [m for m in models if m.kind != "foundation" or m.name in keep]
        dropped = len(models) - len(kept)
        if dropped:
            logger.info(
                "Keeping %d foundation model(s) named by a harness, skipping %d "
                "the account's agents do not use",
                len(keep),
                dropped,
            )
        return kept

    # -- record builders -------------------------------------------------------

    def _foundation(self, s: dict) -> BedrockModel:
        lifecycle = (s.get("modelLifecycle") or {}).get("status", "unknown")
        parts = [
            "Amazon Bedrock foundation model.",
            f"Provider: {s.get('providerName') or 'unknown'}.",
            f"Model ID: {s['modelId']}.",
            f"Region: {self.region}.",
            f"Input modalities: {_joined(s.get('inputModalities'))}.",
            f"Output modalities: {_joined(s.get('outputModalities'))}.",
            f"Inference types: {_joined(s.get('inferenceTypesSupported'))}.",
            f"Customizations supported: {_joined(s.get('customizationsSupported'))}.",
            f"Lifecycle: {lifecycle}.",
            f"ARN: {s['modelArn']}.",
        ]
        # modelId is the stable, globally unique handle
        # (e.g. "anthropic.claude-3-5-sonnet-20241022-v2:0"); modelName is a
        # display string and is not required by the API.
        return BedrockModel(
            name=s["modelId"],
            summary=" ".join(parts),
            kind="foundation",
            provider=s.get("providerName"),
        )

    def _custom(self, s: dict) -> BedrockModel:
        parts = [
            f"Amazon Bedrock custom model ({s.get('customizationType') or 'unknown'}).",
            f"Base model: {s.get('baseModelName') or s.get('baseModelArn') or 'unknown'}.",
            f"Region: {self.region}.",
            f"Status: {s.get('modelStatus') or 'unknown'}.",
            f"Created: {_day(s.get('creationTime'))}.",
            f"ARN: {s['modelArn']}.",
        ]
        return BedrockModel(name=s["modelName"], summary=" ".join(parts), kind="custom")

    def _imported(self, s: dict) -> BedrockModel:
        parts = [
            "Amazon Bedrock imported model.",
            f"Architecture: {s.get('modelArchitecture') or 'unknown'}.",
            f"Region: {self.region}.",
            f"Instruct supported: {bool(s.get('instructSupported'))}.",
            f"Created: {_day(s.get('creationTime'))}.",
            f"ARN: {s['modelArn']}.",
        ]
        return BedrockModel(
            name=s["modelName"], summary=" ".join(parts), kind="imported"
        )
