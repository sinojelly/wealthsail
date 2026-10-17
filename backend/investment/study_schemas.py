"""Actor-owned research commands; no caller-supplied certified snapshots."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

Generation = Annotated[int, Field(strict=True, ge=1)]
Explanation = Annotated[str, Field(min_length=1, max_length=2000)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)


class StudyScope(_Request):
    direction_id: str | None = Field(
        default=None, alias="directionId", min_length=1, max_length=100
    )
    index_id: str | None = Field(
        default=None, alias="indexId", min_length=1, max_length=100
    )
    instrument_id: UUID | None = Field(default=None, alias="instrumentId")
    fund_code: str | None = Field(
        default=None, alias="fundCode", min_length=1, max_length=32
    )
    fund_market: str | None = Field(
        default=None, alias="fundMarket", min_length=1, max_length=32
    )


class CreateStudyRequest(_Request):
    origin_type: Literal["DAILY_EVENT", "SELECTION"] = Field(alias="originType")
    report_run_id: UUID | None = Field(default=None, alias="reportRunId")
    event_id: str | None = Field(
        default=None, alias="eventId", min_length=1, max_length=120
    )
    scope: StudyScope | None = None
    horizon_days: int = Field(default=30, alias="horizonDays", ge=1, le=365)
    plan_track: Literal["LEFT_VALUE", "RIGHT_TREND"] | None = Field(
        default=None, alias="planTrack"
    )

    @model_validator(mode="after")
    def origin(self):
        if self.origin_type == "DAILY_EVENT":
            if self.plan_track is not None:
                raise ValueError("a quantitative track requires its own selection")
            if self.report_run_id is None or self.event_id is None:
                raise ValueError("a READY report and its event are required")
        elif (
            self.report_run_id is not None
            or self.event_id is not None
            or not self.scope
            or not any(self.scope.model_dump().values())
        ):
            raise ValueError("selection requires a scope and no report origin")
        return self


class VersionRequest(_Request):
    expected_generation: Generation = Field(alias="expectedGeneration")
    horizon_days: int | None = Field(default=None, alias="horizonDays", ge=1, le=365)
    conditions: list[dict[str, Any]] | None = Field(default=None, max_length=8)
    reason: str = Field(min_length=1, max_length=1000)
    thesis: str | None = Field(default=None, min_length=1, max_length=2000)
    transmission: list[Explanation] | None = Field(default=None, max_length=8)
    counter_factors: list[Explanation] | None = Field(
        default=None, alias="counterFactors", max_length=8
    )
    watch_for: list[Explanation] | None = Field(
        default=None, alias="watchFor", max_length=8
    )
    invalidation_conditions: list[Explanation] | None = Field(
        default=None, alias="invalidationConditions", max_length=8
    )

    @field_validator("reason")
    @classmethod
    def reason_nonempty(cls, value):
        if not value.strip():
            raise ValueError("reason is required")
        return value.strip()


class StatusRequest(_Request):
    expected_generation: Generation = Field(alias="expectedGeneration")
    enabled: StrictBool


class CheckRequest(_Request):
    expected_generation: Generation = Field(alias="expectedGeneration")


class NoteRequest(_Request):
    expected_generation: Generation = Field(alias="expectedGeneration")
    version_id: UUID | None = Field(default=None, alias="versionId")
    kind: Literal["DECISION", "REVIEW", "ACK"]
    action: Literal["WATCH", "PLAN_BUY", "PLAN_SELL", "HOLD", "IGNORE"] | None = None
    verdict: Literal["SUPPORTED", "MIXED", "REFUTED", "INCONCLUSIVE"] | None = None
    reason: str | None = Field(default=None, max_length=2000)
    lessons: str | None = Field(default=None, max_length=2000)
    error_categories: list[
        Literal[
            "FACT",
            "TRANSMISSION",
            "EXPECTATIONS",
            "INSTRUMENT",
            "TIMING",
            "RISK",
            "DISCIPLINE",
        ]
    ] = Field(default_factory=list, alias="errorCategories", max_length=7)
    check_id: UUID | None = Field(default=None, alias="checkId")

    @model_validator(mode="after")
    def details(self):
        if self.kind == "ACK" and self.version_id is not None:
            raise ValueError("ACK applies to the current study generation")
        if self.kind == "DECISION" and self.action is None:
            raise ValueError("decision action required")
        if self.kind == "REVIEW" and self.verdict is None:
            raise ValueError("review verdict required")
        if self.kind != "ACK" and not (self.reason or "").strip():
            raise ValueError("reason required")
        if (
            self.kind != "DECISION"
            and self.action is not None
            or self.kind != "REVIEW"
            and self.verdict is not None
        ):
            raise ValueError("note fields do not match kind")
        return self


class TradeLinkRequest(_Request):
    expected_generation: Generation = Field(alias="expectedGeneration")
    fill_id: UUID = Field(alias="fillId")
    version_id: UUID | None = Field(default=None, alias="versionId")
    reason: str = Field(min_length=1, max_length=1000)
