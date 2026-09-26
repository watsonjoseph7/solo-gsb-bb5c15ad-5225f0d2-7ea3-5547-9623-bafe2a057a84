# 胶片数字化帧号复核 API

按扫描顺序接收 2～100 帧的 OCR 帧号识别结果，基于动态规划为每帧指派一个
**严格递增**的帧号：自动舍弃误识别候选、推断未识别帧、标出缺失帧号，并给出
最优方案的代价明细。

另有胶片条方向复核接口：接收按实体顺序排列的 **2～4 条胶片条**（每条至少
1 帧、总计 2～100 帧），在“每条保持原序或整体倒序、条间顺序固定”的所有
方向组合上做同样的最优指派，并返回选定的方向与重排结果。

另有复扫窗口规划接口：在逐帧复核结果之上，把**缺失帧号**与**推断帧**
（`source=inferred`）的指派帧号合并去重为复扫目标，划分成若干互不重叠、
长度受限的连续帧号窗口（每窗成本 = 固定成本 + 窗口帧号数），求覆盖全部
目标的最低总成本方案。

另有歧义核验接口：输入与逐帧复核完全相同，但不只返回字典序最小的一条
最优解，而是给出**每帧在所有最低代价合法序列中可取得的帧号**，并把不能
唯一确定帧号的帧整理成核验清单，供人工逐条确认。

另有人工核验计划接口：同样沿用逐帧复核的请求与代价规则，以字典序最小
的最低代价指派为**基准**，利用最优路径的**分叉与汇合**关系求出**最少的
人工核验帧**——只要这些帧的真实帧号与基准一致，其他所有同代价合法序列
都被排除；返回基准帧号序列与每个核验帧的位置、ID、预期帧号。

另有人工确认复核接口：同样沿用逐帧复核的请求与代价规则，另收 1～12 条
`{position, frame_number}` 人工确认，把对应扫描位置钉死为确认帧号
（未命中候选的确认帧仍按推断计价），在确认约束下求最低代价指派；确认
本身导致无解时返回 HTTP `422` 与按位置升序、不可再删减的冲突确认集。

- 语言/框架：Python 3.12 + FastAPI + Pydantic v2
- 算法：后缀动态规划（单调队列优化 + 字典序同分裁决）+ 方向组合枚举（最多 16 种）
  + 复扫窗口划分动态规划 + 最优路径 DAG 分叉-汇合区间的最小命中集
  + 人工确认约束下的同一 DP（钉死位置 + 升序删减求冲突确认集）
- 置信分使用 `Decimal` 精确比较（DP 内部统一放大为整数），避免浮点同分误判

## 一条命令启动

需要已安装 Docker（含 Compose 插件），在项目根目录执行：

```bash
docker compose up --build -d
```

一条命令即完成镜像构建、后台启动与健康检查注册，容器内、外端口均为
`8000`（即访问地址 `http://localhost:8000`）。启动后可直接使用六个接口：

- API 地址（基址）：`http://localhost:8000`
- 交互式文档（Swagger UI，六个接口均可在线调试）：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/health>
- 逐帧复核：`POST http://localhost:8000/api/v1/frame-reviews`
- 人工确认复核：`POST http://localhost:8000/api/v1/confirmed-frame-reviews`
- 歧义核验：`POST http://localhost:8000/api/v1/ambiguity-reviews`
- 人工核验计划：`POST http://localhost:8000/api/v1/verification-plans`
- 胶片条方向复核：`POST http://localhost:8000/api/v1/strip-direction-reviews`
- 复扫窗口规划：`POST http://localhost:8000/api/v1/rescan-plans`

停止服务：

```bash
docker compose down
```


## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/frame-reviews` | 逐帧复核：单条扫描序列的帧号指派 |
| `POST` | `/api/v1/confirmed-frame-reviews` | 人工确认复核：在 1～12 条人工确认帧号约束下的帧号指派 |
| `POST` | `/api/v1/ambiguity-reviews` | 歧义核验：每帧在所有最低代价合法序列中的可取帧号 |
| `POST` | `/api/v1/verification-plans` | 人工核验计划：锁定基准指派所需的最少核验帧 |
| `POST` | `/api/v1/strip-direction-reviews` | 胶片条方向复核：2～4 条胶片条的方向选择与帧号指派 |
| `POST` | `/api/v1/rescan-plans` | 复扫窗口规划：把缺失/推断帧号划分为最低成本的连续窗口 |

### `POST /api/v1/frame-reviews`

#### 请求参数

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `first` | int | 固定首帧号 |
| `last` | int | 固定末帧号，必须严格大于 `first` |
| `max_missing_between` | int | 相邻两帧之间最多允许缺帧数，0～10 |
| `frames` | 数组 | 按扫描顺序排列的 2～100 帧 |
| `frames[].id` | string \| int | 扫描帧唯一标识 |
| `frames[].candidates` | 数组 | OCR 候选，可为空 |
| `candidates[].frame_number` | int | OCR 识别帧号 |
| `candidates[].confidence` | number | OCR 置信分，0～100 |

#### 指派规则与代价

- 第 1 帧必须指派为 `first`，最后 1 帧必须指派为 `last`；
- 相邻指派帧号之差 ∈ `[1, max_missing_between + 1]`；
- 命中候选代价 = `100 - 置信分`；未命中任何候选（纯推断）代价 = `100`；
- 相邻两帧之间每缺 1 帧额外加 `30`；
- 取总代价最小的方案；总代价相同时，取**帧号序列字典序最小**的方案；
- 同一帧内同帧号出现多次时，仅保留置信分最高的一条（同分保留下标小者），
  其余作为重复候选舍弃；超出 `[first, last]` 的候选也会被舍弃。
- 在上述约束下无法形成合法序列时返回 HTTP `422`，错误体含
  `error`（`invalid_range` / `no_feasible_sequence`）与中文 `message`。

#### 请求示例

```bash
curl -s -X POST http://localhost:8000/api/v1/frame-reviews \
  -H 'Content-Type: application/json' \
  -d '{
    "first": 10,
    "last": 13,
    "max_missing_between": 2,
    "frames": [
      {"id": "scan-001", "candidates": [{"frame_number": 10, "confidence": 95}]},
      {"id": "scan-002", "candidates": [{"frame_number": 12, "confidence": 90}]},
      {"id": "scan-003", "candidates": [
        {"frame_number": 13, "confidence": 80},
        {"frame_number": 99, "confidence": 70}
      ]}
    ]
  }'
```

#### 响应示例

```json
{
  "first": 10,
  "last": 13,
  "max_missing_between": 2,
  "frame_count": 3,
  "assigned_frame_numbers": [10, 12, 13],
  "total_cost": 65,
  "candidate_cost_total": 35,
  "missing_penalty_total": 30,
  "missing_frame_numbers": [11],
  "missing_frame_count": 1,
  "discarded_candidate_count": 1,
  "frames": [
    {
      "position": 1,
      "id": "scan-001",
      "assigned_frame_number": 10,
      "source": "candidate",
      "matched_candidate_index": 0,
      "assigned_cost": 5,
      "missing_after": 1,
      "missing_penalty": 30,
      "discarded_candidates": []
    },
    {
      "position": 2,
      "id": "scan-002",
      "assigned_frame_number": 12,
      "source": "candidate",
      "matched_candidate_index": 0,
      "assigned_cost": 10,
      "missing_after": 0,
      "missing_penalty": 0,
      "discarded_candidates": []
    },
    {
      "position": 3,
      "id": "scan-003",
      "assigned_frame_number": 13,
      "source": "candidate",
      "matched_candidate_index": 0,
      "assigned_cost": 20,
      "missing_after": 0,
      "missing_penalty": 0,
      "discarded_candidates": [
        {"candidate_index": 1, "frame_number": 99, "confidence": 70, "reason": "out_of_range"}
      ]
    }
  ]
}
```

舍弃候选的 `reason`：

- `not_selected`：在最优方案中未被采用（典型误识别）；
- `out_of_range`：帧号超出固定的 `[first, last]` 范围；
- `duplicate_frame_number`：同帧内与更高置信分的候选帧号重复。

#### 无合法序列示例

帧号区间无法容纳全部扫描帧（如 3 帧却只给了 2 个帧号）：

```json
{
  "error": "no_feasible_sequence",
  "message": "帧号区间 [1, 2] 只有 2 个帧号，无法为 3 帧指派严格递增的帧号"
}
```

### `POST /api/v1/confirmed-frame-reviews`

用于人工已经核对过部分扫描帧真实帧号的场景。**请求体沿用逐帧复核的全部
字段与代价规则**（`first` / `last` / `max_missing_between` / `frames`，
首末帧固定、严格递增、相邻缺帧上限、候选/推断/缺帧代价与同分裁决完全
一致），另收一个 `confirmations` 数组：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `confirmations` | 数组 | **1～12 条**人工确认，按扫描位置钉死真实帧号 |
| `confirmations[].position` | int | 扫描顺序位置，**从 1 开始**，同一位置不得重复确认 |
| `confirmations[].frame_number` | int | 人工确认的真实帧号，必须落在 `[first, last]` 内 |

指派规则：

- 被确认位置只能取确认帧号；其余位置照常求解；
- 确认帧号即使出现在该帧的 OCR 候选中也按正常命中计价
  （`100 - 置信分`）；**未命中任何候选的确认帧按推断计价（`100`）**，
  即人工确认不改变代价规则，只是缩小可行解空间；
- 确认位置 `1`（必须等于 `first`）与最后一个位置（必须等于 `last`）
  同样允许确认：确认值与固定首末帧一致时为冗余确认、不影响结果，
  不一致时计入冲突确认集；
- 在确认约束下取**总代价最小**的合法序列；同分时仍取**帧号序列字典序
  最小**的方案；
- 成功响应与逐帧复核的响应**完全相同**（逐帧指派、舍弃候选、缺失帧号、
  各项代价汇总等全部字段）。

错误语义：

- 请求参数非法（确认条数不在 1～12、`position` 越界或重复、
  `frame_number` 不在 `[first, last]` 等）返回 HTTP `422`，
  错误码 `invalid_range`；
- **没有任何确认时**基础问题已无合法序列，沿用逐帧复核的
  `no_feasible_sequence`（HTTP `422`）；
- 基础问题有解、但确认集合导致无合法序列时，返回 HTTP `422`，错误码
  **`conflicting_confirmations`**，错误体另含
  `conflicting_confirmations`：**按位置升序、不可再删减的冲突确认集**。
  计算方式：从确认全集按 `position` 升序逐条尝试删除——删除后仍然无解，
  说明该确认并非造成无解的必要项，永久删除；删除后变得可行则保留。
  最终保留下来的每一条，都是从冲突集中单独删去就会恢复可行的“关键”
  确认，可直接交回人工重新核对。

#### 请求示例

沿用歧义核验的 4 帧例子：无确认时第 3 帧在总代价同为 `220` 的最优序列中
可取 13 或 14。人工确认其真实帧号为 14（该帧没有 OCR 候选，仍按推断
计价 100），最优序列即被钉死为 `[10, 11, 14, 16]`：

```bash
curl -s -X POST http://localhost:8000/api/v1/confirmed-frame-reviews \
  -H 'Content-Type: application/json' \
  -d '{
    "first": 10,
    "last": 16,
    "max_missing_between": 2,
    "frames": [
      {"id": "scan-001", "candidates": [{"frame_number": 10, "confidence": 95}]},
      {"id": "scan-002", "candidates": [{"frame_number": 11, "confidence": 90}]},
      {"id": "scan-003", "candidates": []},
      {"id": "scan-004", "candidates": [{"frame_number": 16, "confidence": 85}]}
    ],
    "confirmations": [
      {"position": 3, "frame_number": 14}
    ]
  }'
```

接口地址为 `http://localhost:8000/api/v1/confirmed-frame-reviews`。

#### 响应示例

响应结构与逐帧复核完全相同；注意 `scan-003` 虽为人工确认，但因未命中
候选，`source` 仍为 `inferred`、`assigned_cost` 为 `100`：

```json
{
  "first": 10,
  "last": 16,
  "max_missing_between": 2,
  "frame_count": 4,
  "assigned_frame_numbers": [10, 11, 14, 16],
  "total_cost": 220,
  "candidate_cost_total": 130,
  "missing_penalty_total": 90,
  "missing_frame_numbers": [12, 13, 15],
  "missing_frame_count": 3,
  "discarded_candidate_count": 0,
  "frames": [
    {
      "position": 1,
      "id": "scan-001",
      "assigned_frame_number": 10,
      "source": "candidate",
      "matched_candidate_index": 0,
      "assigned_cost": 5,
      "missing_after": 0,
      "missing_penalty": 0,
      "discarded_candidates": []
    },
    {
      "position": 2,
      "id": "scan-002",
      "assigned_frame_number": 11,
      "source": "candidate",
      "matched_candidate_index": 0,
      "assigned_cost": 10,
      "missing_after": 2,
      "missing_penalty": 60,
      "discarded_candidates": []
    },
    {
      "position": 3,
      "id": "scan-003",
      "assigned_frame_number": 14,
      "source": "inferred",
      "matched_candidate_index": null,
      "assigned_cost": 100,
      "missing_after": 1,
      "missing_penalty": 30,
      "discarded_candidates": []
    },
    {
      "position": 4,
      "id": "scan-004",
      "assigned_frame_number": 16,
      "source": "candidate",
      "matched_candidate_index": 0,
      "assigned_cost": 15,
      "missing_after": 0,
      "missing_penalty": 0,
      "discarded_candidates": []
    }
  ]
}
```

#### 冲突确认示例

3 帧、`first=1`、`last=5`、`max_missing_between=1`：末帧固定为 5，却把
第 3 帧确认为 3——第 3 帧到末帧（同一帧）的矛盾使任何合法序列都不存在。
基础问题本身有解，故返回 `conflicting_confirmations`；第 2 帧确认为 2
删除后仍无解（矛盾只来自第 3 帧），在逐条删减时被永久剔除，最终冲突集
只保留 `position=3` 一条：

```bash
curl -s -X POST http://localhost:8000/api/v1/confirmed-frame-reviews \
  -H 'Content-Type: application/json' \
  -d '{
    "first": 1,
    "last": 5,
    "max_missing_between": 1,
    "frames": [
      {"id": "scan-001", "candidates": []},
      {"id": "scan-002", "candidates": []},
      {"id": "scan-003", "candidates": []}
    ],
    "confirmations": [
      {"position": 2, "frame_number": 2},
      {"position": 3, "frame_number": 3}
    ]
  }'
```

```json
{
  "error": "conflicting_confirmations",
  "message": "人工确认帧号与首末帧固定、严格递增及相邻缺帧上限约束冲突，不存在满足全部确认的合法帧号序列",
  "conflicting_confirmations": [
    {"position": 3, "frame_number": 3}
  ]
}
```

当多条确认各自可行、**合在一起**才冲突时（例如确认值之间的帧号差超出
中间位置能跨越的上限），返回的冲突集会包含多条、且按 `position` 升序
排列；其中每一条都是不可再删减的关键确认，即单独删去任一条都会恢复
可行。

### `POST /api/v1/ambiguity-reviews`

用于人工核验尚不能唯一确定帧号的扫描帧。**请求体与逐帧复核完全相同**
（同样的 `first` / `last` / `max_missing_between` / `frames` 字段与校验
规则，可直接复用逐帧复核的请求 JSON），首末帧固定、相邻缺帧上限、候选/
缺帧代价与全局最优规则也完全一致。

与逐帧复核的区别：逐帧复核在同代价时只回溯出**字典序最小的一条**最优
序列；本接口则枚举全部最优合法序列，给出每帧在其中可取得的**所有**帧号
（按整数放大后的精确代价比较，小数置信分同分不会被漏判，不存在浮点误差，
也不是仅返回字典序首解）。

#### 请求示例

```bash
curl -s -X POST http://localhost:8000/api/v1/ambiguity-reviews \
  -H 'Content-Type: application/json' \
  -d '{
    "first": 10,
    "last": 16,
    "max_missing_between": 2,
    "frames": [
      {"id": "scan-001", "candidates": [{"frame_number": 10, "confidence": 95}]},
      {"id": "scan-002", "candidates": [{"frame_number": 11, "confidence": 90}]},
      {"id": "scan-003", "candidates": []},
      {"id": "scan-004", "candidates": [{"frame_number": 16, "confidence": 85}]}
    ]
  }'
```

#### 响应示例

`scan-003` 没有候选、按推断计代价 100：无论取 13 还是 14，其后到 16 的
缺帧总数不变（整条序列固定缺 3 帧），两条序列总代价同为最低值 220。

```json
{
  "first": 10,
  "last": 16,
  "max_missing_between": 2,
  "frame_count": 4,
  "total_cost": 220,
  "frames": [
    {"position": 1, "id": "scan-001", "possible_frame_numbers": [10]},
    {"position": 2, "id": "scan-002", "possible_frame_numbers": [11]},
    {"position": 3, "id": "scan-003", "possible_frame_numbers": [13, 14]},
    {"position": 4, "id": "scan-004", "possible_frame_numbers": [16]}
  ],
  "review_checklist": [
    {"position": 3, "id": "scan-003", "possible_frame_numbers": [13, 14]}
  ]
}
```

字段说明：

- `total_cost`：与逐帧复核一致的全局最低代价（候选代价 + 缺帧代价）；
- `frames[].possible_frame_numbers`：该帧在**所有最低代价合法序列**中可
  取得的帧号，升序排列；首帧恒为 `[first]`、末帧恒为 `[last]`；
- `review_checklist`：可取帧号多于一个的帧，即无法由代价规则唯一确定、
  需要人工核验的帧，按扫描位置 `position` 升序，含位置、帧 `id` 与可取
  帧号；全部帧都唯一时为空数组。
- 无合法序列时沿用逐帧复核的 HTTP `422` 响应，错误码为
  `invalid_range` / `no_feasible_sequence`，结构同上。

### `POST /api/v1/verification-plans`

在歧义核验之上更进一步：不是把所有歧义帧都列入人工核验，而是求出**最少
的人工核验帧集合**——以逐帧复核给出的字典序最小的最低代价指派为**基准
指派**，只要核验集里这些帧的真实帧号都与基准一致，其他所有同代价合法
序列就都被排除，基准指派即被唯一锁定。

**请求体与逐帧复核完全相同**（同样的 `first` / `last` /
`max_missing_between` / `frames` 字段与校验规则，可直接复用逐帧复核的
请求 JSON），首末帧固定、相邻缺帧上限、候选/缺帧代价与全局最优规则也
完全一致。

#### 核验集的求解规则

- 全部最低代价合法序列构成一个 DAG：顶点为出现在某条最优序列中的
  （帧，帧号），边为最优转移；基准指派是其中字典序最小的一条路径；
- 若某条最优路径在基准位置 `a` 处**分叉**、位置 `b` 处**汇合**且中间不
  经过基准路径，则核验集必须命中 `(a, b)` 内部的某个位置，否则这条分叉
  路径就是一条与核验结果相容的其他最优序列；反之，命中所有此类
  分叉-汇合区间即可排除全部其他最优序列；
- 因此最少核验帧数 = 全部分叉-汇合区间的**最小命中集**（区间族先过滤为
  极小反链，再用经典贪心求最小点数）；数量相同时取**核验位置序列字典序
  最小**的计划；
- 最优解唯一时没有任何分叉区间，返回空计划（`verification_count = 0`）；
- 错误语义与逐帧复核一致：参数非法返回 `invalid_range`、约束下无合法
  序列返回 `no_feasible_sequence`（均为 HTTP `422`）。

#### 请求示例

```bash
curl -s -X POST http://localhost:8000/api/v1/verification-plans \
  -H 'Content-Type: application/json' \
  -d '{
    "first": 10,
    "last": 20,
    "max_missing_between": 2,
    "frames": [
      {"id": "scan-001", "candidates": [{"frame_number": 10, "confidence": 99}]},
      {"id": "scan-002", "candidates": [{"frame_number": 13, "confidence": 90}]},
      {"id": "scan-003", "candidates": []},
      {"id": "scan-004", "candidates": []},
      {"id": "scan-005", "candidates": [{"frame_number": 17, "confidence": 90}]},
      {"id": "scan-006", "candidates": [{"frame_number": 20, "confidence": 99}]}
    ]
  }'
```

#### 响应示例

该例中 `scan-003` 可取 14/15、`scan-004` 可取 15/16（歧义清单共 2 帧），
但最优序列只有 `[10,13,14,15,17,20]`、`[10,13,14,16,17,20]`、
`[10,13,15,16,17,20]` 三条：只要核验 `scan-004` 的真实帧号确为 15，
后两条即被排除，故最少核验帧数为 1，而不是把 2 个歧义帧全部列入计划。

```json
{
  "first": 10,
  "last": 20,
  "max_missing_between": 2,
  "frame_count": 6,
  "total_cost": 372,
  "assigned_frame_numbers": [10, 13, 14, 15, 17, 20],
  "verification_count": 1,
  "verification_frames": [
    {"position": 4, "id": "scan-004", "expected_frame_number": 15}
  ]
}
```

字段说明：

- `assigned_frame_numbers`：基准指派，即逐帧复核给出的字典序最小的最低
  代价帧号序列；`total_cost` 为与逐帧复核一致的全局最低代价；
- `verification_count` / `verification_frames`：最少人工核验帧的数量与
  清单（按 `position` 升序），每项含扫描位置、帧 `id` 与预期帧号
  `expected_frame_number`（即基准指派帧号）；最优解唯一时
  `verification_count = 0`、`verification_frames` 为空数组。

### `POST /api/v1/strip-direction-reviews`

#### 请求参数

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `first` | int | 固定首帧号 |
| `last` | int | 固定末帧号，必须严格大于 `first` |
| `max_missing_between` | int | 相邻两帧之间最多允许缺帧数，0～10 |
| `strips` | 数组 | 按实体顺序排列的 2～4 条胶片条，**条间顺序固定** |
| `strips[].frames` | 数组 | 该条按扫描顺序录入的帧，至少 1 帧；总计 2～100 帧 |
| `frames[].id` | string \| int | 扫描帧唯一标识 |
| `frames[].candidates` | 数组 | OCR 候选，可为空（结构同逐帧复核） |
| `candidates[].frame_number` | int | OCR 识别帧号 |
| `candidates[].confidence` | number | OCR 置信分，0～100 |

每条胶片条要么**保持原序**（方向 `0`），要么**整条倒序**（方向 `1`，
候选随帧一起翻转）。在所有 2ⁿ（n = 条数，最多 16）种方向组合上做帧号
指派，沿用逐帧复核的全部规则：

- 第 1 个重排帧固定 `first`、最后一个固定 `last`，帧号严格递增；
- 相邻指派帧号之差 ∈ `[1, max_missing_between + 1]`；
- 命中候选代价 = `100 - 置信分`，纯推断代价 = `100`，每缺 1 帧加 `30`。

最优方案的裁决顺序（逐级字典序）：

1. **总代价最小**；
2. 总代价相同时，**倒序条数最少**；
3. 再相同时，**方向向量字典序最小**（各条按条序，正序 `0`、倒序 `1`）；
4. 仍相同时，全局**指派帧号序列字典序最小**。

所有方向组合都不存在合法指派时返回 HTTP `422`，错误码为
`no_feasible_combination`；参数非法为 `invalid_range`。

#### 请求示例

```bash
curl -s -X POST http://localhost:8000/api/v1/strip-direction-reviews \
  -H 'Content-Type: application/json' \
  -d '{
    "first": 1,
    "last": 4,
    "max_missing_between": 1,
    "strips": [
      {"frames": [
        {"id": "A1", "candidates": [{"frame_number": 1, "confidence": 99}]},
        {"id": "A2", "candidates": [{"frame_number": 2, "confidence": 90}]}
      ]},
      {"frames": [
        {"id": "B2", "candidates": [{"frame_number": 4, "confidence": 90}]},
        {"id": "B1", "candidates": [{"frame_number": 3, "confidence": 90}]}
      ]}
    ]
  }'
```

#### 响应示例

```json
{
  "first": 1,
  "last": 4,
  "max_missing_between": 1,
  "strip_count": 2,
  "frame_count": 4,
  "directions": [0, 1],
  "reversed_strip_count": 1,
  "strip_frame_ids": [["A1", "A2"], ["B1", "B2"]],
  "frame_ids": ["A1", "A2", "B1", "B2"],
  "assigned_frame_numbers": [1, 2, 3, 4],
  "total_cost": 31,
  "candidate_cost_total": 31,
  "missing_penalty_total": 0,
  "missing_frame_numbers": [],
  "missing_frame_count": 0,
  "discarded_candidate_count": 0,
  "strips": [
    {
      "index": 1,
      "direction": 0,
      "frame_count": 2,
      "frame_ids": ["A1", "A2"],
      "frames": [
        {
          "position": 1,
          "id": "A1",
          "assigned_frame_number": 1,
          "source": "candidate",
          "matched_candidate_index": 0,
          "assigned_cost": 1,
          "missing_after": 0,
          "missing_penalty": 0,
          "discarded_candidates": []
        },
        {
          "position": 2,
          "id": "A2",
          "assigned_frame_number": 2,
          "source": "candidate",
          "matched_candidate_index": 0,
          "assigned_cost": 10,
          "missing_after": 0,
          "missing_penalty": 0,
          "discarded_candidates": []
        }
      ]
    },
    {
      "index": 2,
      "direction": 1,
      "frame_count": 2,
      "frame_ids": ["B1", "B2"],
      "frames": [
        {
          "position": 3,
          "id": "B1",
          "assigned_frame_number": 3,
          "source": "candidate",
          "matched_candidate_index": 0,
          "assigned_cost": 10,
          "missing_after": 0,
          "missing_penalty": 0,
          "discarded_candidates": []
        },
        {
          "position": 4,
          "id": "B2",
          "assigned_frame_number": 4,
          "source": "candidate",
          "matched_candidate_index": 0,
          "assigned_cost": 10,
          "missing_after": 0,
          "missing_penalty": 0,
          "discarded_candidates": []
        }
      ]
    }
  ],
  "frames": [
    "……与 strips[].frames 内容相同，按全局 position 1～4 平铺，字段同逐帧复核"
  ]
}
```

字段说明：

- `directions`：各条选定方向（与输入 `strips` 同序），`0`=保持原序、
  `1`=整体倒序；`reversed_strip_count` 为其中倒序条数；
- `strip_frame_ids` / `strips[].frame_ids`：每条按选定方向**重排后的帧
  ID**；`frame_ids` 是全部帧拼接（条间固定顺序）后的结果；
- `strips[].frames[]` 为该条逐帧指派，其中 `position` 是全局位置；
  顶层 `frames` 则是全部帧按全局顺序展开的同一明细；
- 代价汇总、`missing_*`、`discarded_candidates` 字段含义与逐帧复核一致。

#### 无可行方向组合示例

```json
{
  "error": "no_feasible_combination",
  "message": "2 条胶片条的 4 种方向组合均无法形成合法的严格递增帧号序列"
}
```

### `POST /api/v1/rescan-plans`

在逐帧复核结果之上规划复扫窗口：取复核得到的**缺失帧号**与
**`source=inferred` 帧的指派帧号**，合并去重为复扫目标，再把目标划分成
若干互不重叠的连续帧号窗口，使总成本最低。

#### 请求参数

包含逐帧复核的全部字段（`first` / `last` / `max_missing_between` /
`frames`，含义与校验规则相同），另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `max_window_length` | int | 单个复扫窗口最多覆盖的连续帧号数，1～50 |
| `setup_cost` | number | 每开启一个复扫窗口的固定成本，0～1000 |

#### 窗口划分规则与成本

- 复扫目标 = 缺失帧号 ∪ 推断帧（`source=inferred`）的指派帧号
  （二者天然互斥，合并后去重、升序）；
- 每个窗口是一段连续帧号区间 `[start, end]`，长度
  `end - start + 1 ≤ max_window_length`；窗口之间互不重叠，且全部目标
  都被某个窗口覆盖；
- 每窗成本 = `setup_cost + 窗口帧号数`，总成本为各窗成本之和；
- 最优窗口的起止帧号必然都是目标帧号（把窗口收紧到窗内首个/末个目标
  只会更便宜），因此等价于把有序目标划分成若干连续段；
- 裁决顺序：**总成本最小** → **窗口数最少** → **窗口起止点序列字典序
  最小**；
- 没有复扫目标时返回空 `windows` 与 `total_cost = 0`；
- 错误语义与逐帧复核一致：参数非法返回 `invalid_range`、约束下无合法
  序列返回 `no_feasible_sequence`（均为 HTTP `422`）。

#### 请求示例

```bash
curl -s -X POST http://localhost:8000/api/v1/rescan-plans \
  -H 'Content-Type: application/json' \
  -d '{
    "first": 10,
    "last": 16,
    "max_missing_between": 2,
    "max_window_length": 3,
    "setup_cost": 10,
    "frames": [
      {"id": "scan-001", "candidates": [{"frame_number": 10, "confidence": 95}]},
      {"id": "scan-002", "candidates": [{"frame_number": 12, "confidence": 90}]},
      {"id": "scan-003", "candidates": []},
      {"id": "scan-004", "candidates": [{"frame_number": 16, "confidence": 85}]}
    ]
  }'
```

该例的复核结果为 `scan-003` 被推断为帧号 13（`source=inferred`），缺失
帧号为 11、14、15，合并去重后的复扫目标即 `[11, 13, 14, 15]`。

#### 响应示例

```json
{
  "max_window_length": 3,
  "setup_cost": 10,
  "target_frame_numbers": [11, 13, 14, 15],
  "target_count": 4,
  "window_count": 2,
  "total_cost": 24,
  "windows": [
    {
      "start": 11,
      "end": 11,
      "length": 1,
      "cost": 11,
      "missing_frame_numbers": [11],
      "inferred_frame_ids": []
    },
    {
      "start": 13,
      "end": 15,
      "length": 3,
      "cost": 13,
      "missing_frame_numbers": [14, 15],
      "inferred_frame_ids": ["scan-003"]
    }
  ]
}
```

字段说明：

- `target_frame_numbers` / `target_count`：合并去重后的复扫目标及其数量；
- `windows[].start` / `end`：窗口起止帧号（均含），`length` 为窗口覆盖的
  帧号数（`end - start + 1`），`cost` 为该窗成本
  （`setup_cost + length`）；
- `windows[].missing_frame_numbers`：该窗所含的缺失帧号（升序）；
- `windows[].inferred_frame_ids`：该窗所含推断帧的帧 ID（按指派帧号
  升序）；
- `total_cost`：全部窗口成本之和；无复扫目标时 `windows` 为空、
  `total_cost` 为 `0`。

## 算法说明

令 `span = last - first`，第 i 帧的指派帧号写作 `first + d_i`。
合法条件：`d_0 = 0`、`d_{n-1} = span`、
`1 ≤ d_{i+1} - d_i ≤ max_missing_between + 1`。

后缀 DP（`app/solver.py`）：

```
dp[i][d] = match(i, d)
           + min over d' ∈ [d+1, d+max_step]  (30·(d'-d-1) + dp[i+1][d'])

match(i, d) = 100 - 置信分   若该帧存在帧号 first+d 的候选
            = 100            否则（推断）
```

- 转移最小值改写为滑动窗口（`30·d' + dp[i+1][d']` 在定宽窗口内取最小），
  用单调队列维护，单层 DP 为 O(span)，单方向组合整体
  O(n · span)；方向组合最多 16 个；
- 回溯时在保持最优代价的前提下每一步选最小偏移量，后缀最优与历史无关，
  因此贪心回溯得到的就是字典序最小的最优帧号序列；
- 歧义核验（`/api/v1/ambiguity-reviews`）再对称地计算前缀 DP：
  `prefix[i][d]` 表示第 i 帧取 `first+d` 时第 0..i 帧的最小代价（同样用
  单调队列、O(n·span)）。帧 i 取偏移 d 能出现在某条全局最优合法序列中，
  当且仅当 `prefix[i][d] + suffix[i][d] - match(i,d) = 全局最优代价`
  （拼接处的 match 代价被前缀、后缀各计一次，故减去一份）。枚举满足该
  等式的偏移即得到每帧的全部可取帧号，比较全程在整数缩放域完成，小数
  置信分的同分判定与逐帧复核完全一致；
- 人工核验计划（`/api/v1/verification-plans`）把全部最优合法序列看作
  DAG：顶点为满足上述等式的 (帧, 偏移)，边为满足
  `suffix[i][d] = match(i,d) + 30·(d'-d-1) + suffix[i+1][d']` 的最优
  转移——DAG 上从 (0, `first`) 到 (n-1, `last`) 的每条路径都恰好对应一条
  最低代价合法序列。相对基准路径（字典序最小的最优序列）按位置递推位
  掩码，求出所有“在位置 a 分叉、位置 b 汇合且中间不经过基准路径”的
  区间：核验集有效当且仅当命中每个此类区间的内部。区间族过滤为极小
  反链后，最小命中集大小由经典贪心（取最右内部点）求得，字典序最小的
  最小命中集再借助后缀贪心计数逐位构造；整体复杂度 O(n·span·max_step)；
- 人工确认复核（`/api/v1/confirmed-frame-reviews`）复用同一后缀/前缀
  DP，仅引入 `allowed(i, d)` 闸口：被确认位置只放行确认偏移，其余位置
  全部放行；钉死点之间（含固定首末帧）先做严格递增与相邻缺帧上限的
  前置可行性检查，DP 与字典序最小回溯规则不变，故复杂度仍为
  O(n·span)。确认导致无解时，从确认全集按位置升序逐条试删（每次试删
  重跑一次带约束 DP）：删除后仍无解即永久删除，最终留下的就是不可再
  删减的冲突确认集；最多 12 条确认，试删至多 12 次。
- 胶片条方向复核把每条按候选方向翻转后拼接（条间顺序不变），对每个方向
  向量跑一次上述 DP，再按“总代价 → 倒序条数 → 方向向量 → 帧号序列”
  逐级取字典序最小。

复扫窗口规划（`solve_rescan_plan`）：

- 最优窗口的起止必然落在目标帧号上，问题等价于把 m 个有序目标划分为
  连续段，且每段对应的帧号跨度 ≤ `max_window_length`；
- `dp[j]` 记录覆盖前 j 个目标的最优键 `(总成本, 窗口数, 起止点序列)`，
  转移时枚举最后一窗的起点，三元组直接按裁决顺序比较（向前缀追加同一
  窗口时该序保持单调，故子问题最优可转移），复杂度
  `O(m · max_window_length)`；
- `setup_cost` 与窗口成本同样用 `Decimal` 精确计算。

## 本地开发（不使用 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py       # FastAPI 入口与错误处理
│   ├── schemas.py    # Pydantic 请求/响应模型
│   └── solver.py     # DP 核心（纯标准库，可独立调用）
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```
