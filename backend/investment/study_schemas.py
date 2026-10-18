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
    theme_code: (
        Literal[
            "HAINAN_FTP",
            "PUDONG_DEV",
            "GEO_CRUDE_OIL",
            "AI_MEMORY_HBM",
            "COMMERCIAL_SPACE",
        ]
        | None
    ) = Field(default=None, alias="themeCode")
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
    origin_type: Literal[
        "DAILY_EVENT", "SELECTION", "DAILY_DIRECTION", "DAILY_QUANT_SCAN"
    ] = Field(alias="originType")
    report_run_id: UUID | None = Field(default=None, alias="reportRunId")
    event_id: str | None = Field(
        default=None, alias="eventId", min_length=1, max_length=120
    )
    scope: StudyScope | None = None
    value_direction_key: str | None = Field(
        default=None, alias="valueDirectionKey", pattern=r"^[a-z][a-z0-9_-]{0,63}$"
    )
    subject_kind: Literal["index", "fund", "nav"] | None = Field(
        default=None, alias="subjectKind"
    )
    subject_id: str | None = Field(
        default=None, alias="subjectId", min_length=1, max_length=100
    )
    horizon_days: int = Field(default=30, alias="horizonDays", ge=1, le=365)
    plan_track: Literal["LEFT_VALUE", "RIGHT_TREND"] | None = Field(
        default=None, alias="planTrack"
    )

    @model_validator(mode="after")
    def origin(self):
        if self.origin_type == "DAILY_QUANT_SCAN":
            if (
                self.report_run_id is None
                or self.subject_kind not in {"nav", "fund"}
                or self.subject_id is None
                or self.plan_track is None
                or {"scope", "event_id", "value_direction_key"}.intersection(
                    self.model_fields_set
                )
            ):
                raise ValueError(
                    "scan tracking requires an exact report, fund and track without caller context"
                )
            return self
        direction_fields = (
            self.value_direction_key,
            self.subject_kind,
            self.subject_id,
        )
        if self.origin_type == "DAILY_DIRECTION":
            if (
                not all(direction_fields)
                or self.report_run_id is None
                or self.plan_track is None
                or {"scope", "event_id"}.intersection(self.model_fields_set)
            ):
                raise ValueError(
                    "direction tracking requires an exact report, direction, subject and track without caller scope"
                )
            return self
        if any(value is not None for value in direction_fields):
            raise ValueError("direction identity requires a DAILY_DIRECTION origin")
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
