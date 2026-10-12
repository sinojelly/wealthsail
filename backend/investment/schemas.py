from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScreenQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    keyword: str = Field(default="", max_length=100)
    direction_id: str | None = Field(default=None, alias="directionId", max_length=80)
    index_id: str | None = Field(default=None, alias="indexId", max_length=80)
    fund_type: str | None = Field(default=None, alias="fundType", max_length=80)
    trade_form: Literal["ETF", "FUND"] | None = Field(default=None, alias="tradeForm")
    nav_position_min: float | None = Field(default=None, alias="navPositionMin", ge=0, le=100)
    nav_position_max: float | None = Field(default=None, alias="navPositionMax", ge=0, le=100)
    sort_by: Literal["name", "code", "navPosition"] = Field(default="name", alias="sortBy")
    sort_order: Literal["asc", "desc"] = Field(default="asc", alias="sortOrder")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, alias="pageSize", ge=1, le=100)

    @model_validator(mode="after")
    def validate_range(self):
        if self.nav_position_min is not None and self.nav_position_max is not None:
            if self.nav_position_min > self.nav_position_max:
                raise ValueError("净值位置下限不能大于上限")
        return self
