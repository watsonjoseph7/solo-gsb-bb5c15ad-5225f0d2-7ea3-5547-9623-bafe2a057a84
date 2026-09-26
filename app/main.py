"""FastAPI 应用入口。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.schemas import (
    AmbiguityReviewResponse,
    ConfirmedFrameReviewRequest,
    ErrorResponse,
    RescanPlanRequest,
    RescanPlanResponse,
    ReviewRequest,
    ReviewResponse,
    StripDirectionReviewRequest,
    StripDirectionReviewResponse,
    VerificationPlanResponse,
)
from app.solver import (
    ConflictingConfirmationsError,
    InvalidRangeError,
    NoFeasibleCombinationError,
    NoFeasibleSequenceError,
    solve_ambiguity_review,
    solve_confirmed_frame_review,
    solve_rescan_plan,
    solve_review,
    solve_strip_directions,
    solve_verification_plan,
)

app = FastAPI(
    title="胶片数字化帧号复核 API",
    description=(
        "对扫描顺序的 2～100 帧 OCR 帧号进行复核：用动态规划指派严格递增的"
        "帧号，舍弃误识别候选并推断未识别帧，输出缺失帧号与代价明细。"
        "并支持 2～4 条胶片条的方向复核：枚举各条正序/倒序组合，"
        "在合法指派中取总代价最小的方案。"
        "还可基于复核结果规划复扫窗口：把缺失帧号与推断帧号合并为复扫目标，"
        "划分成固定成本加长度计费的连续窗口，求最低总成本方案。"
        "歧义核验接口则返回每帧在所有最低代价合法序列中可取得的帧号，"
        "供人工确认尚不能唯一确定帧号的扫描帧。"
        "人工核验计划接口进一步利用最优路径的分叉与汇合关系，"
        "求出能唯一锁定基准指派的最少人工核验帧集合。"
        "人工确认复核接口则接收 1～12 条人工确认帧，把对应位置钉死为"
        "确认帧号（未命中候选仍按推断计价），在确认约束下求最低代价指派，"
        "确认导致无解时返回不可再删减的冲突确认集。"
    ),
    version="1.5.0",
)


@app.exception_handler(InvalidRangeError)
async def invalid_range_handler(_request: Request, exc: InvalidRangeError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": exc.code, "message": exc.message},
    )


@app.exception_handler(NoFeasibleSequenceError)
async def no_feasible_handler(
    _request: Request, exc: NoFeasibleSequenceError
) -> JSONResponse:
    # 输入本身合法，但约束下不存在合法序列 -> 422 语义校验失败
    return JSONResponse(
        status_code=422,
        content={"error": exc.code, "message": exc.message},
    )


@app.exception_handler(NoFeasibleCombinationError)
async def no_feasible_combination_handler(
    _request: Request, exc: NoFeasibleCombinationError
) -> JSONResponse:
    # 所有胶片条方向组合均不存在合法指派 -> 422 语义校验失败
    return JSONResponse(
        status_code=422,
        content={"error": exc.code, "message": exc.message},
    )


@app.exception_handler(ConflictingConfirmationsError)
async def conflicting_confirmations_handler(
    _request: Request, exc: ConflictingConfirmationsError
) -> JSONResponse:
    # 基础问题有解、但人工确认集合导致无合法序列 -> 422，附冲突确认集
    return JSONResponse(
        status_code=422,
        content={
            "error": exc.code,
            "message": exc.message,
            "conflicting_confirmations": exc.conflicts,
        },
    )


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/frame-reviews",
    response_model=ReviewResponse,
    tags=["review"],
    summary="复核扫描帧的帧号指派",
    responses={422: {"model": ErrorResponse, "description": "参数非法或无合法序列"}},
)
async def review_frames(request: ReviewRequest) -> ReviewResponse:
    result = solve_review(request.model_dump())
    return ReviewResponse.model_validate(result)


@app.post(
    "/api/v1/confirmed-frame-reviews",
    response_model=ReviewResponse,
    tags=["review"],
    summary="在人工确认帧号约束下复核扫描帧的帧号指派",
    responses={
        422: {
            "model": ErrorResponse,
            "description": (
                "参数非法、无确认时已无合法序列，或人工确认相互冲突"
            ),
        }
    },
)
async def review_confirmed_frames(
    request: ConfirmedFrameReviewRequest,
) -> ReviewResponse:
    result = solve_confirmed_frame_review(request.model_dump())
    return ReviewResponse.model_validate(result)


@app.post(
    "/api/v1/ambiguity-reviews",
    response_model=AmbiguityReviewResponse,
    tags=["review"],
    summary="核验扫描帧在最优序列中的可取帧号（歧义清单）",
    responses={422: {"model": ErrorResponse, "description": "参数非法或无合法序列"}},
)
async def review_ambiguity(request: ReviewRequest) -> AmbiguityReviewResponse:
    result = solve_ambiguity_review(request.model_dump())
    return AmbiguityReviewResponse.model_validate(result)


@app.post(
    "/api/v1/verification-plans",
    response_model=VerificationPlanResponse,
    tags=["review"],
    summary="规划锁定基准指派所需的最少人工核验帧",
    responses={422: {"model": ErrorResponse, "description": "参数非法或无合法序列"}},
)
async def plan_verification(request: ReviewRequest) -> VerificationPlanResponse:
    result = solve_verification_plan(request.model_dump())
    return VerificationPlanResponse.model_validate(result)


@app.post(
    "/api/v1/strip-direction-reviews",
    response_model=StripDirectionReviewResponse,
    tags=["review"],
    summary="复核多条胶片条的方向与帧号指派",
    responses={
        422: {
            "model": ErrorResponse,
            "description": "参数非法，或所有方向组合均无合法指派",
        }
    },
)
async def review_strip_directions(
    request: StripDirectionReviewRequest,
) -> StripDirectionReviewResponse:
    result = solve_strip_directions(request.model_dump())
    return StripDirectionReviewResponse.model_validate(result)


@app.post(
    "/api/v1/rescan-plans",
    response_model=RescanPlanResponse,
    tags=["rescan"],
    summary="规划缺失/推断帧的复扫窗口",
    responses={422: {"model": ErrorResponse, "description": "参数非法或无合法序列"}},
)
async def plan_rescan(request: RescanPlanRequest) -> RescanPlanResponse:
    result = solve_rescan_plan(request.model_dump())
    return RescanPlanResponse.model_validate(result)
