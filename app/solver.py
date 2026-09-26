"""帧号指派的动态规划核心。

问题描述
--------
扫描得到 n 帧（按扫描顺序排列），每帧有 0 个或多个 OCR 候选帧号及置信分。
已知整卷胶片的首帧号 ``first``、末帧号 ``last``，以及相邻两帧之间最多允许
跳过 ``max_missing_between`` 个胶片帧号。

- 候选命中代价：100 - 置信分（0～100）
- 该帧没有命中任何候选（纯推断）：100
- 相邻两帧之间每缺 1 帧：+30

要求给每帧指派一个严格递增的帧号，使总代价最小；总代价相同时，帧号序列
按字典序取最小。第一帧指派为 ``first``、最后一帧指派为 ``last``。

胶片条方向复核（:func:`solve_strip_directions`）在此基础上再枚举 2～4 条
胶片条各自的方向（原序 / 整条倒序），条间顺序固定，在所有方向组合的合法
指派中按总代价、倒序条数、方向向量、指派帧号序列依次裁决。

人工确认复核（:func:`solve_confirmed_frame_review`）在同一 DP 上把若干
位置钉死为人工确认帧号：被确认位置只保留对应偏移，其余规则（首末帧
固定、严格递增、相邻缺帧上限、代价与字典序同分裁决）完全不变。

复扫窗口规划（:func:`solve_rescan_plan`）复用复核结果，把缺失帧号与
source=inferred 帧的指派帧号合并去重为复扫目标，再划分成互不重叠、
长度受限的连续帧号窗口，按总成本、窗口数、起止点序列依次裁决。

歧义核验（:func:`solve_ambiguity_review`）复用同一后缀 DP，再补一个对称
的前缀 DP：帧 i 取偏移 d 能出现在某个全局最优合法序列中，当且仅当
``prefix[i][d] + suffix[i][d] - match_cost(i, d)`` 等于全局最优代价。
据此给出每帧在**所有**最优合法序列中可取得的帧号集合，而不是仅回溯出
字典序最小的一条。

人工核验计划（:func:`solve_verification_plan`）把全部最优合法序列看作
DAG：顶点为出现在某条最优序列中的 (帧, 帧号)，边为最优转移。相对基准
指派（字典序最小的最优序列）求所有“分叉-汇合”区间：若存在一条最优路径
在基准位置 a 处分叉、位置 b 处汇合且中间不经过基准路径，则核验集必须
命中 (a, b) 内部的某个位置，否则该分叉路径就是一条与核验结果相容的
其他最优序列；反之命中所有此类区间即可排除全部其他最优序列。于是最少
核验帧数 = 区间族的最小命中集（经典贪心），数量相同时按位置序列字典序
最小逐位构造。

实现思路
--------
令 span = last - first，第 i 帧（0-based）指派到 first + d_i。
合法序列满足 d_0 = 0、d_{n-1} = span 且
1 <= d_i - d_{i-1} <= max_missing_between + 1。

定义后缀 DP：

    dp[i][d] = 第 i 帧取 first + d 时，第 i..n-1 帧的最小总代价（不含 i 之前
               的缺帧代价）

转移：

    dp[i][d] = match_cost(i, d)
             + min over d' in [d+1, d+max_step] of
                   (30 * (d' - d - 1) + dp[i+1][d'])

把转移改写成滑动窗口最小值：

    30 * (d' - d - 1) + dp[i+1][d']
      = (30 * d' + dp[i+1][d']) - 30 * (d + 1)

按 d 从大到小扫描时，窗口 [d+1, d+max_step] 整体平移，用单调队列维护
``30 * d' + dp[i+1][d']`` 的最小值，单层 DP 复杂度降为 O(span)。

回溯时在保持最小代价的前提下每一步选最小的 d，由于后缀代价与历史无关，
这恰好得到字典序最小的最优序列。

置信分可能带小数，统一用 Decimal 精确比较，避免浮点同分误判。
"""

from __future__ import annotations

from bisect import bisect_left
from collections import deque
from decimal import Decimal
from typing import Any


# ---- 代价常量 ----
INFER_COST = Decimal("100")        # 未命中任何候选时的推断代价
MISSING_FRAME_PENALTY = 30         # 每缺 1 帧的额外代价
CONFIDENCE_BASE = Decimal("100")   # 候选代价 = 100 - 置信分

# 业务边界
MIN_FRAMES = 2
MAX_FRAMES = 100
MIN_MISSING_GAP = 0
MAX_MISSING_GAP = 10

# 胶片条方向复核的边界
MIN_STRIPS = 2
MAX_STRIPS = 4

# 复扫窗口规划的边界
MIN_WINDOW_LENGTH = 1
MAX_WINDOW_LENGTH = 50
MIN_SETUP_COST = Decimal("0")
MAX_SETUP_COST = Decimal("1000")

# 方向编码：正序=0，倒序=1
DIRECTION_FORWARD = 0
DIRECTION_REVERSED = 1


# 人工确认复核的边界
MIN_CONFIRMATIONS = 1
MAX_CONFIRMATIONS = 12


class ReviewError(Exception):
    """业务校验失败的基类，携带机器可读的 code。"""

    code = "review_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidRangeError(ReviewError):
    code = "invalid_range"


class NoFeasibleSequenceError(ReviewError):
    code = "no_feasible_sequence"


class NoFeasibleCombinationError(ReviewError):
    """胶片条的所有方向组合都不存在合法指派。"""

    code = "no_feasible_combination"


class ConflictingConfirmationsError(ReviewError):
    """无确认时存在合法序列，但人工确认集合导致不存在合法序列。"""

    code = "conflicting_confirmations"

    def __init__(self, message: str, conflicts: list[dict[str, int]]):
        super().__init__(message)
        # 不可再删减的冲突确认集（按 position 升序）
        self.conflicts = conflicts


def _to_decimal(value: Any, field: str, range_desc: str = "0～100") -> Decimal:
    """把 JSON 中的数字转成 Decimal；拒绝 NaN/Infinity 等非法数值。"""
    try:
        result = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - 统一转成业务错误
        raise InvalidRangeError(
            f"{field} 必须是 {range_desc} 之间的有限数值"
        ) from exc
    if not result.is_finite():
        raise InvalidRangeError(f"{field} 必须是 {range_desc} 之间的有限数值")
    return result


def _json_number(value: Any) -> Any:
    """把 Decimal 转回 JSON 友好的 int/float。"""
    if isinstance(value, Decimal):
        integer = value.to_integral_value()
        if integer == value:
            return int(integer)
        return float(value)
    return value


# ---- 输入校验与候选解析 ----


def _validate_endpoints(payload: dict[str, Any]) -> tuple[int, int, int]:
    """校验并返回 (first, last, max_missing_between)。"""
    first = payload.get("first")
    last = payload.get("last")
    gap = payload.get("max_missing_between")

    if not isinstance(first, int) or isinstance(first, bool):
        raise InvalidRangeError("first 必须是整数帧号")
    if not isinstance(last, int) or isinstance(last, bool):
        raise InvalidRangeError("last 必须是整数帧号")
    if last <= first:
        raise InvalidRangeError("last 必须严格大于 first（帧号按扫描方向递增）")
    if (
        not isinstance(gap, int)
        or isinstance(gap, bool)
        or not (MIN_MISSING_GAP <= gap <= MAX_MISSING_GAP)
    ):
        raise InvalidRangeError(
            f"max_missing_between 必须是 {MIN_MISSING_GAP}～{MAX_MISSING_GAP} 的整数"
        )
    return first, last, gap


def _parse_frames(
    frames: Any,
    *,
    frame_label: str = "帧",
) -> list[tuple[Any, list[dict[str, Any]]]]:
    """解析帧列表并清洗候选。

    ``frame_label`` 用于在错误信息中定位（如 "第 2 条胶片条的第 1 帧"）。
    返回 [(frame_id, cleaned_candidates)]，其中 cleaned 候选保留输入下标。
    """
    parsed: list[tuple[Any, list[dict[str, Any]]]] = []
    for i, frame in enumerate(frames):
        label = f"{frame_label}第 {i + 1} 帧"
        if not isinstance(frame, dict) or "id" not in frame:
            raise InvalidRangeError(f"{label}缺少 id 字段")
        frame_id = frame["id"]
        raw_candidates = frame.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raise InvalidRangeError(f"帧 {frame_id!r} 的 candidates 必须是数组")

        cleaned: list[dict[str, Any]] = []
        for j, cand in enumerate(raw_candidates):
            if not isinstance(cand, dict):
                raise InvalidRangeError(
                    f"帧 {frame_id!r} 的第 {j + 1} 个候选格式不正确"
                )
            number = cand.get("frame_number")
            if not isinstance(number, int) or isinstance(number, bool):
                raise InvalidRangeError(
                    f"帧 {frame_id!r} 的第 {j + 1} 个候选 frame_number 必须是整数"
                )
            confidence = _to_decimal(
                cand.get("confidence"),
                f"帧 {frame_id!r} 的第 {j + 1} 个候选 confidence",
            )
            if not (Decimal("0") <= confidence <= CONFIDENCE_BASE):
                raise InvalidRangeError(
                    f"帧 {frame_id!r} 的第 {j + 1} 个候选 confidence 必须在 0～100 之间"
                )
            cleaned.append(
                {
                    "index": j,
                    "frame_number": number,
                    "confidence": confidence,
                }
            )
        parsed.append((frame_id, cleaned))
    return parsed


def _best_candidates(
    parsed: list[tuple[Any, list[dict[str, Any]]]],
) -> list[dict[int, tuple[Decimal, int]]]:
    """逐帧把同帧号候选压成最高置信分一条（同分保留下标小者）。"""
    best_by_frame: list[dict[int, tuple[Decimal, int]]] = []
    for _frame_id, candidates in parsed:
        best: dict[int, tuple[Decimal, int]] = {}
        for cand in candidates:
            number = cand["frame_number"]
            confidence = cand["confidence"]
            idx = cand["index"]
            incumbent = best.get(number)
            if incumbent is None or confidence > incumbent[0]:
                best[number] = (confidence, idx)
        best_by_frame.append(best)
    return best_by_frame


# ---- DP 核心 ----


def _cost_scale(parsed: list[tuple[Any, list[dict[str, Any]]]]) -> int:
    """求所有置信分的最大小数位数 p，返回 10**p。

    代价乘以该倍率后全部是整数，DP 内可用 int 精确比较，同分裁决语义
    与 Decimal 完全一致；输出时再除回去。
    """
    places = 0
    for _frame_id, candidates in parsed:
        for cand in candidates:
            exponent = cand["confidence"].as_tuple().exponent
            if isinstance(exponent, int) and exponent < 0:
                places = max(places, -exponent)
    return 10**places


def _solve_assignment(
    first: int,
    last: int,
    gap: int,
    parsed: list[tuple[Any, list[dict[str, Any]]]],
    fixed_offsets: dict[int, int] | None = None,
) -> dict[str, Any]:
    """对一条已按扫描顺序排好的帧序列执行帧号指派。

    ``fixed_offsets`` 给出被人工确认钉死的 {帧下标: 偏移}（偏移 =
    确认帧号 - first）；这些位置只允许取对应偏移，其余位置不受限制。
    首末帧本就固定为 offset 0 / span，钉死成其他值等价于无解。

    返回内部解法（代价为乘以 cost_scale 的整数），供各个接口各自组装
    响应；无合法序列时抛 NoFeasibleSequenceError。
    """
    fixed_offsets = fixed_offsets or {}
    n = len(parsed)
    span = last - first
    max_step = gap + 1  # 相邻指派帧号之差上限（差 1 表示不缺帧）

    best_by_frame = _best_candidates(parsed)

    def allowed(i: int, d: int) -> bool:
        """该位置是否允许取该偏移（人工确认钉死的位置只放行确认值）。"""
        forced = fixed_offsets.get(i)
        return forced is None or forced == d

    # ---- 可行性前置检查 ----
    if span < n - 1:
        raise NoFeasibleSequenceError(
            f"帧号区间 [{first}, {last}] 只有 {span + 1} 个帧号，"
            f"无法为 {n} 帧指派严格递增的帧号"
        )
    if span > (n - 1) * max_step:
        raise NoFeasibleSequenceError(
            f"帧号跨度为 {span}，但 {n} 帧在相邻最多缺 {gap} 帧的限制下"
            f"最多只能跨越 {(n - 1) * max_step}，无法覆盖到末帧 {last}"
        )
    # 钉死位置之间（含首末帧）需满足严格递增与相邻缺帧上限
    pinned = sorted(
        {(0, 0), (n - 1, span)}
        | {(i, d) for i, d in fixed_offsets.items()}
    )
    for (i1, d1), (i2, d2) in zip(pinned, pinned[1:]):
        step_span = d2 - d1
        frame_gap = i2 - i1
        if step_span < frame_gap:
            raise NoFeasibleSequenceError(
                f"第 {i2 + 1} 帧确认帧号 {first + d2} 与第 {i1 + 1} 帧"
                f"（{first + d1}）之间只有 {step_span} 个帧号差，"
                f"无法容纳中间的 {frame_gap - 1} 帧严格递增指派"
            )
        if step_span > frame_gap * max_step:
            raise NoFeasibleSequenceError(
                f"第 {i1 + 1} 帧（{first + d1}）与第 {i2 + 1} 帧确认帧号"
                f" {first + d2} 相差 {step_span}，但中间 {frame_gap - 1} 帧"
                f"在相邻最多缺 {gap} 帧的限制下最多只能跨越"
                f" {frame_gap * max_step}"
            )

    # ---- 代价统一放大成整数：推断 100、缺帧 30、命中 100-置信分 ----
    scale = _cost_scale(parsed)
    infer_cost_scaled = int(INFER_COST * scale)
    penalty_scaled = MISSING_FRAME_PENALTY * scale
    hit_costs: list[dict[int, int]] = []
    for best in best_by_frame:
        hit_costs.append(
            {
                number: int((CONFIDENCE_BASE - confidence) * scale)
                for number, (confidence, _idx) in best.items()
            }
        )

    def match_cost(i: int, d: int) -> int:
        return hit_costs[i].get(first + d, infer_cost_scaled)

    # ---- 后缀 DP：layers[i] = dp[i]，dict[offset -> 后缀最小代价] ----
    layers: list[dict[int, int]] = [dict() for _ in range(n)]

    # 最后一帧必须指派 last（offset = span）
    if allowed(n - 1, span):
        layers[n - 1][span] = match_cost(n - 1, span)

    for i in range(n - 2, -1, -1):
        layer: dict[int, int] = {}
        # 单调队列元素 (offset, key)，按 d 从大到小扫描：
        # - 新 offset（较小）从队尾加入，超窗的大 offset 从队首淘汰；
        # - key = 30*offset + dp[i+1][offset]，从队首到队尾严格递增，
        #   队首即窗口最小值。
        # 等价于以 x = span - offset 为坐标的经典递增滑动窗口。
        queue: deque[tuple[int, int]] = deque()
        next_layer = layers[i + 1]
        for d in range(span - 1, -1, -1):
            added = d + 1
            suffix = next_layer.get(added)
            if suffix is not None:
                key = penalty_scaled * added + suffix
                while queue and queue[-1][1] >= key:
                    queue.pop()
                queue.append((added, key))
            # 窗口上界为 d + max_step，丢掉 offset 过大的队首
            if queue and queue[0][0] > d + max_step:
                queue.popleft()
            # 被人工确认钉死的位置只保留确认偏移
            if queue and allowed(i, d):
                # min key - 30*(d+1) 即为最优后继带来的代价
                best_transition = queue[0][1] - penalty_scaled * (d + 1)
                layer[d] = match_cost(i, d) + best_transition
        layers[i] = layer

    # 第一帧必须指派 first（offset = 0）
    if 0 not in layers[0]:
        # 理论上已被前置可行性检查覆盖，保留防御性报错。
        raise NoFeasibleSequenceError("无法形成从首帧到末帧的合法帧号序列")

    total_cost = layers[0][0]

    # 对称的前缀 DP：prefix[i][d] = 第 i 帧取 offset d 时，第 0..i 帧的
    # 最小总代价（不含 i 之后的缺帧代价）。第一帧固定 offset 0。
    prefix = _prefix_layers(
        n, span, max_step, penalty_scaled, match_cost, allowed
    )

    return {
        "first": first,
        "last": last,
        "gap": gap,
        "span": span,
        "n": n,
        "parsed": parsed,
        "best_by_frame": best_by_frame,
        "offsets": _backtrack_offsets(
            layers, span, max_step, penalty_scaled, allowed
        ),
        "total_cost": total_cost,
        "cost_scale": scale,
        "suffix_layers": layers,
        "prefix_layers": prefix,
        "match_cost_fn": match_cost,
    }


def _prefix_layers(
    n: int,
    span: int,
    max_step: int,
    penalty_scaled: int,
    match_cost: Any,
    allowed: Any,
) -> list[dict[int, int]]:
    prefix: list[dict[int, int]] = [dict() for _ in range(n)]
    if allowed(0, 0):
        prefix[0][0] = match_cost(0, 0)
    for i in range(1, n):
        layer: dict[int, int] = {}
        # 单调队列元素 (offset, key)，按 d 从小到大扫描：
        # - 新 offset（前驱 d-1）从队尾加入，过小（出窗）的从队首淘汰；
        # - key = -30*offset + prefix[i-1][offset]，队首到队尾严格递增，
        #   队首即前驱窗口最小值。
        queue: deque[tuple[int, int]] = deque()
        prev_layer = prefix[i - 1]
        for d in range(1, span + 1):
            added = d - 1
            prev = prev_layer.get(added)
            if prev is not None:
                key = -penalty_scaled * added + prev
                while queue and queue[-1][1] >= key:
                    queue.pop()
                queue.append((added, key))
            # 窗口下界为 d - max_step，丢掉 offset 过小的队首
            if queue and queue[0][0] < d - max_step:
                queue.popleft()
            if queue and allowed(i, d):
                best_transition = queue[0][1] + penalty_scaled * (d - 1)
                layer[d] = match_cost(i, d) + best_transition
        prefix[i] = layer
    return prefix


def _backtrack_offsets(
    layers: list[dict[int, int]],
    span: int,
    max_step: int,
    penalty_scaled: int,
    allowed: Any,
) -> list[int]:
    """在后缀 DP 上回溯字典序最小的最优指派偏移序列。"""
    n = len(layers)
    offsets: list[int] = [0]
    for i in range(n - 1):
        d = offsets[-1]
        chosen: int | None = None
        chosen_value: int | None = None
        upper = min(span, d + max_step)
        next_layer = layers[i + 1]
        for nd in range(d + 1, upper + 1):
            if not allowed(i + 1, nd):
                continue  # 人工确认钉死的位置只回溯到确认偏移
            suffix = next_layer.get(nd)
            if suffix is None:
                continue
            value = penalty_scaled * (nd - d - 1) + suffix
            if chosen_value is None or value < chosen_value:
                chosen_value = value
                chosen = nd
        if chosen is None:  # 防御性检查
            raise NoFeasibleSequenceError(
                f"回溯到第 {i + 1} 帧时找不到合法后继帧号"
            )
        offsets.append(chosen)
    return offsets


# ---- 逐帧结果与代价明细 ----


def _build_frame_results(
    solution: dict[str, Any],
    *,
    position_offset: int = 0,
) -> tuple[list[dict[str, Any]], Decimal, int, list[int], list[int]]:
    """把内部解法组装成逐帧 JSON 结构。

    position_offset 用于胶片条场景让 position 取全局位置。
    返回 (frames, 命中/推断代价合计(Decimal), 舍弃候选总数,
    指派帧号序列, 各帧后缺帧数)。
    """
    first = solution["first"]
    n = solution["n"]
    offsets = solution["offsets"]
    parsed = solution["parsed"]
    best_by_frame = solution["best_by_frame"]

    result_frames: list[dict[str, Any]] = []
    sum_match_cost = Decimal(0)
    total_discarded = 0

    missing_after_list: list[int] = []
    for i in range(n):
        if i + 1 < n:
            missing_after = offsets[i + 1] - offsets[i] - 1
        else:
            missing_after = 0
        missing_after_list.append(missing_after)

    for i, (frame_id, candidates) in enumerate(parsed):
        d = offsets[i]
        number = first + d
        hit = best_by_frame[i].get(number)
        matched_index = hit[1] if hit is not None else None

        if hit is not None:
            # 直接用置信分计算精确代价，避免整数缩放反除带来的精度风险
            frame_cost = CONFIDENCE_BASE - hit[0]
            source = "candidate"
        else:
            frame_cost = INFER_COST
            source = "inferred"
        sum_match_cost += frame_cost

        missing_after = missing_after_list[i]
        gap_penalty = MISSING_FRAME_PENALTY * missing_after

        discarded: list[dict[str, Any]] = []
        for cand in candidates:
            idx = cand["index"]
            cand_number = cand["frame_number"]
            if idx == matched_index:
                continue  # 被采用的候选
            # 说明舍弃原因
            winner = best_by_frame[i].get(cand_number)
            if winner is not None and winner[1] != idx:
                reason = "duplicate_frame_number"  # 同帧号仅保留置信分最高者
            elif cand_number < first or cand_number > solution["last"]:
                reason = "out_of_range"            # 超出固定首末帧号范围
            else:
                reason = "not_selected"            # 误识别 / 未被最优方案采用
            discarded.append(
                {
                    "candidate_index": idx,
                    "frame_number": cand_number,
                    "confidence": _json_number(cand["confidence"]),
                    "reason": reason,
                }
            )

        discarded.sort(key=lambda item: item["candidate_index"])
        total_discarded += len(discarded)

        result_frames.append(
            {
                "position": position_offset + i + 1,
                "id": frame_id,
                "assigned_frame_number": number,
                "source": source,
                "matched_candidate_index": matched_index,
                "assigned_cost": _json_number(frame_cost),
                "missing_after": missing_after,
                "missing_penalty": gap_penalty,
                "discarded_candidates": discarded,
            }
        )

    assigned_numbers = [first + d for d in offsets]
    return (
        result_frames,
        sum_match_cost,
        total_discarded,
        assigned_numbers,
        missing_after_list,
    )


def _missing_frame_numbers(
    first: int, last: int, assigned_set: set[int]
) -> list[int]:
    """[first, last] 内未指派给任何扫描帧的帧号（有序）。"""
    return [num for num in range(first, last + 1) if num not in assigned_set]


# ---- 对外接口 ----


def _assemble_review_result(
    first: int,
    last: int,
    gap: int,
    solution: dict[str, Any],
) -> dict[str, Any]:
    """把内部指派解法组装成逐帧复核响应（逐帧复核与人工确认复核共用）。"""
    n = solution["n"]

    (
        result_frames,
        sum_match_cost,
        total_discarded,
        assigned_numbers,
        _missing_after_list,
    ) = _build_frame_results(solution)

    sum_missing_penalty = Decimal(
        MISSING_FRAME_PENALTY * (solution["span"] - (n - 1))
    )
    total_cost = sum_match_cost + sum_missing_penalty

    missing_frame_numbers = _missing_frame_numbers(
        first, last, set(assigned_numbers)
    )

    assert total_cost == Decimal(solution["total_cost"]) / solution["cost_scale"], (
        "代价明细与整数 DP 总代价不一致"
    )
    assert len(missing_frame_numbers) == solution["span"] - (n - 1), (
        "缺失帧计数不一致"
    )

    return {
        "first": first,
        "last": last,
        "max_missing_between": gap,
        "frame_count": n,
        "assigned_frame_numbers": assigned_numbers,
        "total_cost": _json_number(total_cost),
        "candidate_cost_total": _json_number(sum_match_cost),
        "missing_penalty_total": _json_number(sum_missing_penalty),
        "missing_frame_numbers": missing_frame_numbers,
        "missing_frame_count": len(missing_frame_numbers),
        "discarded_candidate_count": total_discarded,
        "frames": result_frames,
    }


def solve_review(payload: dict[str, Any]) -> dict[str, Any]:
    """执行帧号复核。

    入参字段：
        first / last: int，固定首末帧号（last > first）
        max_missing_between: int，0～10
        frames: [{id, candidates: [{frame_number: int, confidence: 0~100}]}]

    返回逐帧指派、每帧舍弃的候选、缺失帧号与各项代价汇总。
    参数不合法时抛 InvalidRangeError，无合法序列时抛 NoFeasibleSequenceError。
    """
    frames = payload.get("frames")
    if not isinstance(frames, list) or not (MIN_FRAMES <= len(frames) <= MAX_FRAMES):
        raise InvalidRangeError(
            f"frames 数量必须在 {MIN_FRAMES}～{MAX_FRAMES} 之间"
        )

    first, last, gap = _validate_endpoints(payload)
    parsed = _parse_frames(frames)
    solution = _solve_assignment(first, last, gap, parsed)
    return _assemble_review_result(first, last, gap, solution)


def _parse_confirmations(
    raw: Any,
    n: int,
    first: int,
    last: int,
) -> dict[int, int]:
    """校验人工确认列表，返回 {帧下标(0-based): 确认帧号}。

    接受 1～12 条 {position, frame_number}：position 从 1 起且不得重复，
    frame_number 必须为落在 [first, last] 的整数。
    """
    if not isinstance(raw, list) or not (
        MIN_CONFIRMATIONS <= len(raw) <= MAX_CONFIRMATIONS
    ):
        raise InvalidRangeError(
            f"confirmations 数量必须在 {MIN_CONFIRMATIONS}～{MAX_CONFIRMATIONS} 之间"
        )

    confirmed: dict[int, int] = {}
    for j, item in enumerate(raw):
        label = f"第 {j + 1} 条确认"
        if not isinstance(item, dict):
            raise InvalidRangeError(f"{label}格式不正确，需包含 position 与 frame_number")
        position = item.get("position")
        if not isinstance(position, int) or isinstance(position, bool):
            raise InvalidRangeError(f"{label}的 position 必须是整数")
        if not (1 <= position <= n):
            raise InvalidRangeError(
                f"{label}的 position 必须在 1～{n} 之间（当前 {position}）"
            )
        frame_number = item.get("frame_number")
        if not isinstance(frame_number, int) or isinstance(frame_number, bool):
            raise InvalidRangeError(f"{label}的 frame_number 必须是整数")
        if not (first <= frame_number <= last):
            raise InvalidRangeError(
                f"{label}（position={position}）的 frame_number 必须落在"
                f" [{first}, {last}] 内（当前 {frame_number}）"
            )
        index = position - 1
        if index in confirmed:
            raise InvalidRangeError(
                f"position={position} 的确认重复：每个位置至多确认一次"
            )
        confirmed[index] = frame_number
    return confirmed


def solve_confirmed_frame_review(payload: dict[str, Any]) -> dict[str, Any]:
    """执行带人工确认的帧号复核。

    入参为 :func:`solve_review` 的全部字段，另加
        confirmations: 1～12 条 {position, frame_number}，
        position 从 1 起且不得重复，frame_number 落在 [first, last]。

    被确认位置只能指派确认帧号；未命中该帧候选时仍按推断计价（100），
    其余位置按原规则求解：首末帧固定、严格递增、相邻缺帧上限不变，
    取总代价最小、同分时帧号序列字典序最小的方案，响应明细与逐帧复核
    完全相同。无确认时已无解沿用 no_feasible_sequence；确认本身导致
    无解时抛 ConflictingConfirmationsError（422 /
    conflicting_confirmations），携带按位置升序、不可再删减的冲突确认集。
    """
    frames = payload.get("frames")
    if not isinstance(frames, list) or not (MIN_FRAMES <= len(frames) <= MAX_FRAMES):
        raise InvalidRangeError(
            f"frames 数量必须在 {MIN_FRAMES}～{MAX_FRAMES} 之间"
        )

    first, last, gap = _validate_endpoints(payload)
    n = len(frames)
    parsed = _parse_frames(frames)
    confirmed = _parse_confirmations(payload.get("confirmations"), n, first, last)

    # 先求无确认解：基础问题本身无解时沿用 no_feasible_sequence
    _solve_assignment(first, last, gap, parsed)

    fixed_offsets = {index: number - first for index, number in confirmed.items()}
    try:
        solution = _solve_assignment(first, last, gap, parsed, fixed_offsets)
    except NoFeasibleSequenceError:
        # 从全集按位置升序逐条尝试删除：删除后仍无解就永久删除，
        # 删除后变得可行则恢复保留；最终留下的即不可再删减的冲突确认集。
        retained: dict[int, int] = dict(fixed_offsets)
        for index in sorted(fixed_offsets):
            trial = {i: d for i, d in retained.items() if i != index}
            try:
                _solve_assignment(first, last, gap, parsed, trial)
            except NoFeasibleSequenceError:
                retained.pop(index)  # 删了仍然无解 -> 永久删除
        conflicts = [
            {"position": index + 1, "frame_number": first + retained[index]}
            for index in sorted(retained)
        ]
        raise ConflictingConfirmationsError(
            "人工确认帧号与首末帧固定、严格递增及相邻缺帧上限约束冲突，"
            "不存在满足全部确认的合法帧号序列",
            conflicts,
        ) from None

    return _assemble_review_result(first, last, gap, solution)


def solve_ambiguity_review(payload: dict[str, Any]) -> dict[str, Any]:
    """列出每帧在所有全局最优合法序列中可取得的帧号，供人工核验歧义。

    入参与 :func:`solve_review` 完全相同（复用同一请求校验与 DP）。
    区别在于不再只回溯字典序最小的一条最优序列，而是结合前缀 DP 与
    后缀 DP 求每帧的可达最优帧号集合：

        prefix[i][d] + suffix[i][d] - match_cost(i, d) == 全局最优代价

    成立当且仅当存在一条总代价最优的合法序列使第 i 帧取 first + d。
    所有比较都在按置信分小数位放大后的整数域进行，同分判定与逐帧复核
    完全一致，不会因浮点或仅取单一回溯解而漏报歧义。

    返回全局最低代价、每帧可取帧号升序列表，以及可取帧号多于一个的帧
    按扫描位置组成的核验清单。错误语义同 solve_review。
    """
    frames = payload.get("frames")
    if not isinstance(frames, list) or not (MIN_FRAMES <= len(frames) <= MAX_FRAMES):
        raise InvalidRangeError(
            f"frames 数量必须在 {MIN_FRAMES}～{MAX_FRAMES} 之间"
        )

    first, last, gap = _validate_endpoints(payload)
    parsed = _parse_frames(frames)
    solution = _solve_assignment(first, last, gap, parsed)

    n = solution["n"]
    total_cost_scaled = solution["total_cost"]
    scale = solution["cost_scale"]
    prefix_layers = solution["prefix_layers"]
    suffix_layers = solution["suffix_layers"]
    match_cost = solution["match_cost_fn"]

    result_frames: list[dict[str, Any]] = []
    review_checklist: list[dict[str, Any]] = []

    for i, (frame_id, _candidates) in enumerate(parsed):
        prefix_layer = prefix_layers[i]
        suffix_layer = suffix_layers[i]
        possible_offsets: list[int] = []
        # 前缀层的键即可参与拼接的偏移；逐一套用最优拼接等式，键顺序天然升序
        for d, prefix_cost in prefix_layer.items():
            suffix_cost = suffix_layer.get(d)
            if suffix_cost is None:
                continue
            if prefix_cost + suffix_cost - match_cost(i, d) == total_cost_scaled:
                possible_offsets.append(d)

        if not possible_offsets:
            # 字典序最优回溯路径上的偏移必然满足等式，故每帧至少一个可取帧号
            raise NoFeasibleSequenceError(
                f"第 {i + 1} 帧在最优序列中没有可取帧号"
            )

        possible_numbers = [first + d for d in possible_offsets]
        result_frames.append(
            {
                "position": i + 1,
                "id": frame_id,
                "possible_frame_numbers": possible_numbers,
            }
        )
        if len(possible_numbers) > 1:
            review_checklist.append(
                {
                    "position": i + 1,
                    "id": frame_id,
                    "possible_frame_numbers": possible_numbers,
                }
            )

    total_cost = Decimal(total_cost_scaled) / scale
    return {
        "first": first,
        "last": last,
        "max_missing_between": gap,
        "frame_count": n,
        "total_cost": _json_number(total_cost),
        "frames": result_frames,
        "review_checklist": review_checklist,
    }


# ---- 人工核验计划 ----


def _bypass_intervals(
    n: int,
    possible: list[list[int]],
    baseline: list[int],
    max_step: int,
    penalty_scaled: int,
    match_cost: Any,
    suffix_layers: list[dict[int, int]],
) -> list[tuple[int, int]]:
    """求相对基准路径的全部分叉-汇合区间，并过滤为包含关系极小的反链。

    区间 (a, b)（位置下标，b >= a + 2）表示：存在一条最优路径从基准顶点
    (a, baseline[a]) 分叉、在 (b, baseline[b]) 汇合，且中间位置 a+1..b-1
    全部不经过基准路径。核验集必须命中每个此类区间的内部，否则该分叉
    路径就是一条与核验结果相容的其他最优序列。

    实现：在最优路径 DAG 上按位置递推位掩码。src[d] 的第 a 位置位表示
    存在一条从 (a, baseline[a]) 出发、中间不经过基准顶点的最优路径到达
    当前位置的非基准顶点 d；路径汇入基准顶点 (k, baseline[k]) 时，掩码
    中的每个 a 都产生区间 (a, k)。对每个分叉点 a 只保留最早的汇合点
    （更晚的区间包含它，必然被命中），再过滤为左右端点均严格递增的
    极小反链。
    """
    prev_possible = possible[0]
    prev_src: dict[int, int] = {}
    first_merge: dict[int, int] = {}  # 分叉位置 a -> 最早汇合位置

    for k in range(1, n):
        base_prev = baseline[k - 1]
        base_curr = baseline[k]
        suffix_prev = suffix_layers[k - 1]
        suffix_curr = suffix_layers[k]
        curr_src: dict[int, int] = {}
        merge_mask = 0
        for d in possible[k]:
            suffix_d = suffix_curr[d]
            mask = 0
            for d2 in prev_possible:
                step = d - d2
                if step < 1:
                    break  # prev_possible 升序，之后的前驱只会更大
                if step > max_step:
                    continue
                suffix_d2 = suffix_prev.get(d2)
                if suffix_d2 is None:
                    continue
                # 边 (k-1, d2) -> (k, d) 在某条最优路径上
                if (
                    suffix_d2
                    != match_cost(k - 1, d2)
                    + penalty_scaled * (step - 1)
                    + suffix_d
                ):
                    continue
                if d2 == base_prev:
                    mask |= 1 << (k - 1)  # 在 k-1 处刚从基准路径分叉
                else:
                    mask |= prev_src.get(d2, 0)
            if d == base_curr:
                # 汇入基准顶点；直接基准边 (k-1 -> k) 对应空区间，剔除
                merge_mask = mask & ~(1 << (k - 1))
            else:
                curr_src[d] = mask
        # k 递增扫描，首次记录的汇合点即该分叉点的最早汇合点
        while merge_mask:
            bit = merge_mask & -merge_mask
            first_merge.setdefault(bit.bit_length() - 1, k)
            merge_mask ^= bit
        prev_possible = possible[k]
        prev_src = curr_src

    # 过滤为极小反链：(a, b) 被保留当且仅当不存在 a' > a 使 f(a') <= f(a)
    intervals: list[tuple[int, int]] = []
    min_merge: int | None = None
    for a in sorted(first_merge, reverse=True):
        b = first_merge[a]
        if min_merge is None or b < min_merge:
            intervals.append((a, b))
            min_merge = b
    intervals.reverse()
    return intervals


def _min_hitting_positions(intervals: list[tuple[int, int]]) -> list[int]:
    """求命中全部区间的最少位置集合；数量相同时取位置序列字典序最小。

    区间为按左端点（右端点同步）严格递增的反链 (a, b)，可选择的命中点
    为其整数内部点 a+1 .. b-1。先用经典贪心（取区间最右内部点）自右
    向左求出每个后缀的最少命中点数，再自左向右逐位构造：每一步在首个
    未命中区间的内部从小到大取第一个仍能用剩余预算完成命中的点。
    """
    r = len(intervals)
    if r == 0:
        return []
    lefts = [iv[0] for iv in intervals]
    rights = [iv[1] for iv in intervals]

    # suffix_need[j] = 命中区间 j..r-1 所需的最少点数
    suffix_need = [0] * (r + 1)
    for j in range(r - 1, -1, -1):
        # 贪心点 rights[j] - 1 命中所有左端点小于它的后续区间
        nxt = bisect_left(lefts, rights[j] - 1, j)
        suffix_need[j] = 1 + suffix_need[nxt]

    chosen: list[int] = []
    start = 0
    budget = suffix_need[0]
    while budget > 0:
        # 下一个命中点必须落在首个未命中区间 (lefts[start], rights[start])
        # 内：否则该区间只能由更晚的点命中，而区间内部点都小于它，矛盾。
        for p in range(lefts[start] + 1, rights[start]):
            # p 命中后缀中左端点 < p 的所有区间（右端点递增，必然 > p）
            nxt = bisect_left(lefts, p, start)
            if suffix_need[nxt] == budget - 1:
                chosen.append(p)
                start = nxt
                budget -= 1
                break
        else:  # 防御性检查：贪心点 rights[start]-1 必然可行
            raise NoFeasibleSequenceError("无法构造最少核验位置集合")
    return chosen


def solve_verification_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """规划最少人工核验帧，使基准指派成为唯一与核验相容的最优序列。

    入参与 :func:`solve_review` 完全相同（复用同一请求校验、代价规则与
    DP）。基准指派为逐帧复核给出的字典序最小的最低代价序列。若一组扫描
    帧核验后的真实帧号与基准指派一致时，所有其他同代价合法序列都被
    排除，则称这组帧为有效核验集；本接口返回帧数最少、数量相同时位置
    序列字典序最小的有效核验集，而不是简单地把全部歧义帧列入计划。

    全部最优合法序列构成一个 DAG（顶点为出现在某条最优序列中的
    (帧, 帧号)，边为最优转移）。相对基准路径的每个分叉-汇合区间都
    必须在内部至少核验一帧，否则该分叉路径就是一条与核验结果相容的
    其他最优序列；反之命中所有区间即可排除全部其他最优序列。最少
    核验帧数即区间族的最小命中集。最优解唯一时无分叉区间，返回空
    计划。错误语义与 solve_review 相同。
    """
    frames = payload.get("frames")
    if not isinstance(frames, list) or not (MIN_FRAMES <= len(frames) <= MAX_FRAMES):
        raise InvalidRangeError(
            f"frames 数量必须在 {MIN_FRAMES}～{MAX_FRAMES} 之间"
        )

    first, last, gap = _validate_endpoints(payload)
    parsed = _parse_frames(frames)
    solution = _solve_assignment(first, last, gap, parsed)

    n = solution["n"]
    total_cost_scaled = solution["total_cost"]
    scale = solution["cost_scale"]
    prefix_layers = solution["prefix_layers"]
    suffix_layers = solution["suffix_layers"]
    match_cost = solution["match_cost_fn"]
    baseline_offsets = solution["offsets"]
    max_step = gap + 1
    penalty_scaled = MISSING_FRAME_PENALTY * scale

    # 每帧在所有最优合法序列中的可取偏移（与歧义核验同一判定等式，
    # 键顺序天然升序），即最优路径 DAG 在各位置的顶点集
    possible: list[list[int]] = []
    for i in range(n):
        layer: list[int] = []
        for d, prefix_cost in prefix_layers[i].items():
            suffix_cost = suffix_layers[i].get(d)
            if suffix_cost is None:
                continue
            if prefix_cost + suffix_cost - match_cost(i, d) == total_cost_scaled:
                layer.append(d)
        possible.append(layer)

    intervals = _bypass_intervals(
        n,
        possible,
        baseline_offsets,
        max_step,
        penalty_scaled,
        match_cost,
        suffix_layers,
    )
    plan_indices = _min_hitting_positions(intervals)

    baseline_numbers = [first + d for d in baseline_offsets]
    verification_frames = [
        {
            "position": idx + 1,
            "id": parsed[idx][0],
            "expected_frame_number": baseline_numbers[idx],
        }
        for idx in plan_indices
    ]

    total_cost = Decimal(total_cost_scaled) / scale
    return {
        "first": first,
        "last": last,
        "max_missing_between": gap,
        "frame_count": n,
        "total_cost": _json_number(total_cost),
        "assigned_frame_numbers": baseline_numbers,
        "verification_count": len(verification_frames),
        "verification_frames": verification_frames,
    }


def _parse_strips(
    strips: Any,
) -> list[list[tuple[Any, list[dict[str, Any]]]]]:
    """校验并解析胶片条输入，返回每条解析后的帧序列。"""
    if not isinstance(strips, list) or not (MIN_STRIPS <= len(strips) <= MAX_STRIPS):
        raise InvalidRangeError(
            f"strips 数量必须在 {MIN_STRIPS}～{MAX_STRIPS} 之间"
        )
    parsed_strips: list[list[tuple[Any, list[dict[str, Any]]]]] = []
    total_frames = 0
    for s, strip in enumerate(strips):
        if not isinstance(strip, dict):
            raise InvalidRangeError(f"第 {s + 1} 条胶片条格式不正确")
        frames = strip.get("frames")
        if not isinstance(frames, list) or len(frames) < 1:
            raise InvalidRangeError(
                f"第 {s + 1} 条胶片条必须包含至少 1 帧"
            )
        parsed = _parse_frames(
            frames, frame_label=f"第 {s + 1} 条胶片条的"
        )
        parsed_strips.append(parsed)
        total_frames += len(parsed)
    if not (MIN_FRAMES <= total_frames <= MAX_FRAMES):
        raise InvalidRangeError(
            f"所有胶片条的帧总数必须在 {MIN_FRAMES}～{MAX_FRAMES} 之间"
            f"（当前 {total_frames} 帧）"
        )
    return parsed_strips


def solve_strip_directions(payload: dict[str, Any]) -> dict[str, Any]:
    """执行胶片条方向复核。

    入参字段：
        first / last / max_missing_between: 同 solve_review
        strips: [{frames: [{id, candidates}]}]，2～4 条，
                每条至少 1 帧，总计 2～100 帧。条间顺序固定。

    枚举每条胶片条正序 / 倒序（最多 16 种方向组合），条内倒序为整条
    翻转（候选仍随帧走）。在所有存在合法指派的组合中依次按
    总代价最小、倒序条数最少、方向向量字典序最小、指派帧号序列字典序
    最小裁决。所有组合均不可行时抛 NoFeasibleCombinationError（422）。
    """
    first, last, gap = _validate_endpoints(payload)
    parsed_strips = _parse_strips(payload.get("strips"))
    strip_sizes = [len(strip) for strip in parsed_strips]
    strip_count = len(parsed_strips)

    best_key: tuple[Any, ...] | None = None
    best: dict[str, Any] | None = None

    # 方向向量从全 0 到全 1 按二进制递增枚举，与最终字典序裁决方向一致。
    for mask in range(1 << strip_count):
        directions = tuple(
            DIRECTION_REVERSED if (mask >> (strip_count - 1 - s)) & 1
            else DIRECTION_FORWARD
            for s in range(strip_count)
        )
        ordered: list[tuple[Any, list[dict[str, Any]]]] = []
        for s, strip in enumerate(parsed_strips):
            if directions[s] == DIRECTION_REVERSED:
                ordered.extend(reversed(strip))
            else:
                ordered.extend(strip)

        try:
            solution = _solve_assignment(first, last, gap, ordered)
        except NoFeasibleSequenceError:
            continue

        # 裁决键：总代价、倒序条数、方向向量、指派帧号序列（逐级字典序）
        ranking_key = (
            solution["total_cost"],
            directions.count(DIRECTION_REVERSED),
            directions,
            tuple(first + d for d in solution["offsets"]),
        )
        if best_key is None or ranking_key < best_key:
            best_key = ranking_key
            best = {
                "directions": directions,
                "solution": solution,
            }

    if best is None:
        raise NoFeasibleCombinationError(
            f"{strip_count} 条胶片条的 {1 << strip_count} 种方向组合"
            "均无法形成合法的严格递增帧号序列"
        )

    directions = best["directions"]
    solution = best["solution"]
    n = solution["n"]

    # ---- 组装全局逐帧结果 ----
    (
        all_frames,
        sum_match_cost,
        total_discarded,
        assigned_numbers,
        _missing_after_list,
    ) = _build_frame_results(solution)

    sum_missing_penalty = Decimal(
        MISSING_FRAME_PENALTY * (solution["span"] - (n - 1))
    )
    total_cost = sum_match_cost + sum_missing_penalty
    missing_frame_numbers = _missing_frame_numbers(
        first, last, set(assigned_numbers)
    )
    assert total_cost == Decimal(solution["total_cost"]) / solution["cost_scale"], (
        "代价明细与 DP 总代价不一致"
    )

    global_frame_ids = [frame["id"] for frame in all_frames]

    # ---- 按条切片：方向、重排后的帧 ID、逐帧指派 ----
    result_strips: list[dict[str, Any]] = []
    strip_frame_ids: list[list[Any]] = []
    cursor = 0
    for s, size in enumerate(strip_sizes):
        strip_frames = all_frames[cursor : cursor + size]
        ids = [frame["id"] for frame in strip_frames]
        strip_frame_ids.append(ids)
        result_strips.append(
            {
                "index": s + 1,
                "direction": directions[s],
                "frame_count": size,
                "frame_ids": ids,
                "frames": strip_frames,
            }
        )
        cursor += size

    return {
        "first": first,
        "last": last,
        "max_missing_between": gap,
        "strip_count": strip_count,
        "frame_count": n,
        "directions": list(directions),
        "reversed_strip_count": directions.count(DIRECTION_REVERSED),
        "strip_frame_ids": strip_frame_ids,
        "frame_ids": global_frame_ids,
        "assigned_frame_numbers": assigned_numbers,
        "total_cost": _json_number(total_cost),
        "candidate_cost_total": _json_number(sum_match_cost),
        "missing_penalty_total": _json_number(sum_missing_penalty),
        "missing_frame_numbers": missing_frame_numbers,
        "missing_frame_count": len(missing_frame_numbers),
        "discarded_candidate_count": total_discarded,
        "strips": result_strips,
        "frames": all_frames,
    }


# ---- 复扫窗口规划 ----


def _plan_windows(
    targets: list[int],
    max_window_length: int,
    setup_cost: Decimal,
) -> tuple[Decimal, list[tuple[int, int]]]:
    """把升序去重的目标帧号划分为互不重叠的连续窗口。

    每窗长度（end - start + 1）不超过 ``max_window_length``，成本为
    ``setup_cost + 窗口帧号数``。返回 (最低总成本, [(start, end), ...])；
    同成本时依次取窗口数更少、起止点序列字典序更小的方案。

    最优解的窗口起止必然都是目标帧号（把窗口收紧到窗内首个/末个目标
    只会更便宜），因此问题等价于把有序目标划分成若干连续段，且段对应
    的帧号跨度受限。dp[j] 记录覆盖前 j 个目标的最优键
    (成本, 窗口数, 起止序列)，三元组按裁决顺序直接比较；向前缀最优键
    追加同一窗口时该序保持单调，故子问题最优可转移。
    """
    m = len(targets)
    if m == 0:
        return Decimal(0), []

    # dp[j] = 覆盖 targets[:j] 的最优键 (成本, 窗口数, 窗口起止序列)
    dp: list[tuple[Decimal, int, tuple[tuple[int, int], ...]] | None] = [
        None
    ] * (m + 1)
    dp[0] = (Decimal(0), 0, ())
    for j in range(1, m + 1):
        end = targets[j - 1]
        best: tuple[Decimal, int, tuple[tuple[int, int], ...]] | None = None
        # 最后一窗 [targets[i], end] 覆盖第 i..j-1 个目标（0-based）
        for i in range(j - 1, -1, -1):
            start = targets[i]
            length = end - start + 1
            if length > max_window_length:
                break
            prev = dp[i]
            assert prev is not None, "单目标窗口长度为 1，任意前缀必可达"
            key = (
                prev[0] + setup_cost + length,
                prev[1] + 1,
                prev[2] + ((start, end),),
            )
            if best is None or key < best:
                best = key
        dp[j] = best

    final = dp[m]
    assert final is not None
    return final[0], list(final[2])


def solve_rescan_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """基于逐帧复核结果规划复扫窗口。

    入参字段：solve_review 的全部字段，外加
        max_window_length: int，1～50，单个窗口最多覆盖的连续帧号数
        setup_cost: 0～1000 的数值，每开启一个窗口的固定成本

    复用 solve_review 取得缺失帧号与 source=inferred 帧的指派帧号，
    合并去重为复扫目标；把目标划分为互不重叠、长度不超过
    max_window_length 的连续帧号窗口，每窗成本 = setup_cost + 窗口
    帧号数，求覆盖全部目标的最低总成本；同成本依次取窗口数更少、
    起止点序列字典序更小的方案。没有目标时返回空窗口与零成本。
    错误语义与 solve_review 相同。
    """
    max_window_length = payload.get("max_window_length")
    if (
        not isinstance(max_window_length, int)
        or isinstance(max_window_length, bool)
        or not (MIN_WINDOW_LENGTH <= max_window_length <= MAX_WINDOW_LENGTH)
    ):
        raise InvalidRangeError(
            f"max_window_length 必须是 "
            f"{MIN_WINDOW_LENGTH}～{MAX_WINDOW_LENGTH} 的整数"
        )
    setup_cost = _to_decimal(payload.get("setup_cost"), "setup_cost", "0～1000")
    if not (MIN_SETUP_COST <= setup_cost <= MAX_SETUP_COST):
        raise InvalidRangeError("setup_cost 必须在 0～1000 之间")

    review = solve_review(payload)

    missing_numbers: list[int] = review["missing_frame_numbers"]
    inferred_frames = [
        (frame["assigned_frame_number"], frame["id"])
        for frame in review["frames"]
        if frame["source"] == "inferred"
    ]

    # 缺失帧号与推断帧号天然互斥，仍统一排序去重
    targets = sorted(set(missing_numbers) | {num for num, _ in inferred_frames})

    planned_cost, windows = _plan_windows(targets, max_window_length, setup_cost)

    inferred_id_by_number = {num: frame_id for num, frame_id in inferred_frames}

    result_windows: list[dict[str, Any]] = []
    total_cost = Decimal(0)
    covered: list[int] = []
    for start, end in windows:
        length = end - start + 1
        window_cost = setup_cost + length
        total_cost += window_cost
        window_missing = [num for num in missing_numbers if start <= num <= end]
        window_inferred = [
            num for num in range(start, end + 1) if num in inferred_id_by_number
        ]
        covered.extend(window_missing)
        covered.extend(window_inferred)
        result_windows.append(
            {
                "start": start,
                "end": end,
                "length": length,
                "cost": _json_number(window_cost),
                "missing_frame_numbers": window_missing,
                "inferred_frame_ids": [
                    inferred_id_by_number[num] for num in window_inferred
                ],
            }
        )

    assert total_cost == planned_cost, "窗口成本合计与 DP 最优成本不一致"
    assert sorted(covered) == targets, "窗口未完整覆盖全部复扫目标"

    return {
        "max_window_length": max_window_length,
        "setup_cost": _json_number(setup_cost),
        "target_frame_numbers": targets,
        "target_count": len(targets),
        "window_count": len(result_windows),
        "total_cost": _json_number(total_cost),
        "windows": result_windows,
    }
