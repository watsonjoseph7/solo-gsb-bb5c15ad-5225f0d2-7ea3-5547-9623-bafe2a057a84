"""请求/响应数据模型（Pydantic v2）。"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field

# 帧 ID 允许字符串或整数
FrameId = Union[str, int]


class CandidateIn(BaseModel):
    frame_number: int = Field(..., description="OCR 识别出的帧号")
    confidence: Union[int, float] = Field(
        ..., ge=0, le=100, description="OCR 置信分，0～100"
    )


class FrameIn(BaseModel):
    id: FrameId = Field(..., description="扫描帧的唯一标识")
    candidates: list[CandidateIn] = Field(
        default_factory=list, description="该帧的 OCR 候选，可为空"
    )


class ReviewRequest(BaseModel):
    first: int = Field(..., description="固定首帧号")
    last: int = Field(..., description="固定末帧号（必须严格大于 first）")
    max_missing_between: int = Field(
        ..., ge=0, le=10, description="相邻两帧之间最多允许缺帧的数量，0～10"
    )
    frames: list[FrameIn] = Field(
        ..., min_length=2, max_length=100, description="按扫描顺序排列的 2～100 帧"
    )


class ConfirmationIn(BaseModel):
    position: int = Field(
        ..., ge=1, le=100, description="人工确认的扫描位置，从 1 开始且不得重复"
    )
    frame_number: int = Field(
        ..., description="人工确认的帧号，必须落在 [first, last] 之内"
    )


class ConfirmedReviewRequest(ReviewRequest):
    confirmations: list[ConfirmationIn] = Field(
        ...,
        min_length=1,
        max_length=12,
        description="1～12 条人工确认；position 不得重复，frame_number 落在 [first, last]",
    )


class StripIn(BaseModel):
    frames: list[FrameIn] = Field(
        ..., min_length=1, description="该胶片条按扫描顺序录入的帧，至少 1 帧"
    )


class StripDirectionReviewRequest(BaseModel):
    first: int = Field(..., description="固定首帧号")
    last: int = Field(..., description="固定末帧号（必须严格大于 first）")
    max_missing_between: int = Field(
        ..., ge=0, le=10, description="相邻两帧之间最多允许缺帧的数量，0～10"
    )
    strips: list[StripIn] = Field(
        ...,
        min_length=2,
        max_length=4,
        description="按实体顺序排列的 2～4 条胶片条；每条可整体正序或倒序",
    )


class RescanPlanRequest(BaseModel):
    first: int = Field(..., description="固定首帧号")
    last: int = Field(..., description="固定末帧号（必须严格大于 first）")
    max_missing_between: int = Field(
        ..., ge=0, le=10, description="相邻两帧之间最多允许缺帧的数量，0～10"
    )
    frames: list[FrameIn] = Field(
        ..., min_length=2, max_length=100, description="按扫描顺序排列的 2～100 帧"
    )
    max_window_length: int = Field(
        ..., ge=1, le=50, description="单个复扫窗口最多覆盖的连续帧号数，1～50"
    )
    setup_cost: Union[int, float] = Field(
        ..., ge=0, le=1000, description="每开启一个复扫窗口的固定成本，0～1000"
    )


class DiscardedCandidateOut(BaseModel):
    candidate_index: int = Field(..., description="该候选在输入 candidates 中的下标")
    frame_number: int
    confidence: Union[int, float]
    reason: Literal["duplicate_frame_number", "out_of_range", "not_selected"]


class FrameAssignmentOut(BaseModel):
    position: int = Field(..., description="扫描顺序位置，从 1 开始")
    id: FrameId
    assigned_frame_number: int = Field(..., description="指派给该帧的严格递增帧号")
    source: Literal["candidate", "inferred"] = Field(
        ..., description="candidate=命中 OCR 候选；inferred=未命中、按序列推断"
    )
    matched_candidate_index: Union[int, None] = Field(
        ..., description="命中的候选下标；推断时为 null"
    )
    assigned_cost: Union[int, float] = Field(
        ..., description="该帧的指派代价（命中=100-置信分，推断=100）"
    )
    missing_after: int = Field(..., description="该帧与下一帧之间缺了多少帧")
    missing_penalty: int = Field(..., description="上述缺帧产生的代价（30/帧）")
    discarded_candidates: list[DiscardedCandidateOut]


class ReviewResponse(BaseModel):
    first: int
    last: int
    max_missing_between: int
    frame_count: int
    assigned_frame_numbers: list[int] = Field(
        ..., description="与输入 frames 等长、严格递增的指派帧号序列"
    )
    total_cost: Union[int, float]
    candidate_cost_total: Union[int, float]
    missing_penalty_total: Union[int, float]
    missing_frame_numbers: list[int] = Field(
        ..., description="[first, last] 内未指派给任何扫描帧的缺失帧号"
    )
    missing_frame_count: int
    discarded_candidate_count: int
    frames: list[FrameAssignmentOut]


class AmbiguousFrameOut(BaseModel):
    position: int = Field(..., description="扫描顺序位置，从 1 开始")
    id: FrameId
    possible_frame_numbers: list[int] = Field(
        ...,
        description="该帧在所有最低代价合法序列中可取得的帧号，升序",
        min_length=1,
    )


class AmbiguityReviewItemOut(BaseModel):
    position: int = Field(..., description="扫描顺序位置，从 1 开始")
    id: FrameId
    possible_frame_numbers: list[int] = Field(
        ...,
        description="该帧在所有最低代价合法序列中可取得的帧号（多于一个），升序",
        min_length=2,
    )


class AmbiguityReviewResponse(BaseModel):
    first: int
    last: int
    max_missing_between: int
    frame_count: int
    total_cost: Union[int, float] = Field(
        ..., description="与逐帧复核一致的全局最低代价"
    )
    frames: list[AmbiguousFrameOut] = Field(
        ..., description="按扫描位置逐帧给出可取帧号升序列表"
    )
    review_checklist: list[AmbiguityReviewItemOut] = Field(
        ...,
        description="可取帧号多于一个、需要人工核验的帧，按扫描位置升序",
    )


class VerificationFrameOut(BaseModel):
    position: int = Field(..., description="扫描顺序位置，从 1 开始")
    id: FrameId
    expected_frame_number: int = Field(
        ..., description="核验通过时应确认的帧号（即基准指派帧号）"
    )


class VerificationPlanResponse(BaseModel):
    first: int
    last: int
    max_missing_between: int
    frame_count: int
    total_cost: Union[int, float] = Field(
        ..., description="与逐帧复核一致的全局最低代价"
    )
    assigned_frame_numbers: list[int] = Field(
        ..., description="基准指派：字典序最小的最低代价帧号序列"
    )
    verification_count: int = Field(..., description="人工核验帧的最少数量")
    verification_frames: list[VerificationFrameOut] = Field(
        ...,
        description="最少人工核验帧（position 升序）；最优解唯一时为空数组",
    )


class StripAssignmentOut(BaseModel):
    index: int = Field(..., description="胶片条序号，从 1 开始（条间顺序固定）")
    direction: Literal[0, 1] = Field(
        ..., description="选定方向：0=保持原序，1=整体倒序"
    )
    frame_count: int = Field(..., description="该条胶片条的帧数")
    frame_ids: list[FrameId] = Field(
        ..., description="按选定方向重排后的帧 ID（条内扫描顺序）"
    )
    frames: list[FrameAssignmentOut] = Field(
        ..., description="该条各帧的逐帧指派（position 为全局位置）"
    )


class StripDirectionReviewResponse(BaseModel):
    first: int
    last: int
    max_missing_between: int
    strip_count: int
    frame_count: int
    directions: list[Literal[0, 1]] = Field(
        ...,
        description="各条选定方向（0=正序，1=倒序），下标与 strips 对应",
    )
    reversed_strip_count: int = Field(..., description="选定为倒序的胶片条数量")
    strip_frame_ids: list[list[FrameId]] = Field(
        ..., description="逐条约为 [条][重排后条内帧序] 的帧 ID 二维数组"
    )
    frame_ids: list[FrameId] = Field(
        ..., description="按条间顺序与各条选定方向重排后的全部帧 ID"
    )
    assigned_frame_numbers: list[int] = Field(
        ..., description="与 frame_ids 等长、严格递增的全局指派帧号序列"
    )
    total_cost: Union[int, float]
    candidate_cost_total: Union[int, float]
    missing_penalty_total: Union[int, float]
    missing_frame_numbers: list[int] = Field(
        ..., description="[first, last] 内未指派给任何扫描帧的缺失帧号"
    )
    missing_frame_count: int
    discarded_candidate_count: int
    strips: list[StripAssignmentOut] = Field(
        ..., description="逐条方向、重排帧 ID 与逐帧指派"
    )
    frames: list[FrameAssignmentOut] = Field(
        ..., description="全部帧按全局顺序展开的逐帧指派与代价明细"
    )


class RescanWindowOut(BaseModel):
    start: int = Field(..., description="窗口起始帧号（含）")
    end: int = Field(..., description="窗口结束帧号（含）")
    length: int = Field(..., description="窗口覆盖的帧号数 = end - start + 1")
    cost: Union[int, float] = Field(
        ..., description="该窗成本 = setup_cost + 窗口帧号数"
    )
    missing_frame_numbers: list[int] = Field(
        ..., description="该窗所含的缺失帧号，升序"
    )
    inferred_frame_ids: list[FrameId] = Field(
        ...,
        description="该窗所含推断帧（source=inferred）的帧 ID，按指派帧号升序",
    )


class RescanPlanResponse(BaseModel):
    max_window_length: int
    setup_cost: Union[int, float]
    target_frame_numbers: list[int] = Field(
        ..., description="缺失帧号与推断帧号合并去重后的复扫目标，升序"
    )
    target_count: int
    window_count: int
    total_cost: Union[int, float] = Field(
        ..., description="全部窗口成本之和；无复扫目标时为 0"
    )
    windows: list[RescanWindowOut] = Field(
        ..., description="互不重叠的连续复扫窗口；无复扫目标时为空数组"
    )


class ConflictingConfirmationOut(BaseModel):
    position: int = Field(..., description="冲突确认的扫描位置，从 1 开始")
    frame_number: int = Field(..., description="该位置被人工确认的帧号")


class ErrorResponse(BaseModel):
    error: Literal[
        "invalid_range",
        "no_feasible_sequence",
        "no_feasible_combination",
        "conflicting_confirmations",
    ]
    message: str
    conflicting_confirmations: Union[list[ConflictingConfirmationOut], None] = Field(
        default=None,
        description=(
            "仅 error=conflicting_confirmations 时出现：按 position 升序的"
            "不可再删减冲突确认集"
        ),
    )
