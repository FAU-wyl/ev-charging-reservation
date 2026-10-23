# Ecurious 充电车位预约 Mock Server

这是一个用于黑客马拉松本地开发的、可独立运行的 FastAPI 模拟服务。它依据仓库根目录的 `openapi.yaml` 和 `Ecurious_Mock_API_Documentation.txt` 实现预约、车位状态与事件轮询，并额外提供 `/mock/*` 开发控制接口。

**本项目及两份输入文档均为虚构 Mock，不是 Ecurious 官方 API，也不能控制真实充电设施。** 将来接入企业服务时，仍须以现场官方文档、凭据与安全规则为准。

## 模块架构

```text
backend/
├── app/
│   ├── main.py                    # FastAPI 工厂、路由装配、统一错误响应
│   ├── config.py                  # 环境变量配置
│   ├── api/                       # bays、reservations、events、mock 路由
│   ├── core/                      # 状态机与业务异常
│   ├── db/database.py             # SQLite schema、连接、事务、种子与重置
│   ├── schemas/models.py          # Pydantic 请求/响应模型
│   └── services/                  # 预约、事件、硬件模拟和 No-Show 策略
├── tests/                         # 单元/集成/完整流程及并发测试
├── requirements.txt
├── pytest.ini
└── .env.example
```

数据库初始化三个测试车位：`BAY-001`、`BAY-002`、`BAY-003`。其中 `BAY-002` 被故意设为不支持预约，用于冲突场景测试。

## 安装与启动

需要 Python 3.11 或更高版本。

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
set -a
source .env.example
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

服务地址是 `http://127.0.0.1:8000`，Swagger UI 是 `http://127.0.0.1:8000/docs`，OpenAPI JSON 是 `http://127.0.0.1:8000/openapi.json`。

应用直接读取进程环境变量，不会自动载入 `.env` 文件。开发时可按上面命令 `source .env.example`，或复制后由 IDE/进程管理器载入。

## 配置与认证

| 环境变量 | 默认值 | 含义 |
|---|---:|---|
| `ECURIOUS_MOCK_MODE` | `true` | 为 `false` 时所有 `/mock/*` 接口返回 404 |
| `ECURIOUS_MOCK_TOKEN` | `local-dev-token` | HTTP Bearer Token；不要在生产环境使用默认值 |
| `DATABASE_URL` | `sqlite:///./ecurious_mock.db` | SQLite 数据库 URL |
| `NO_SHOW_GRACE_SECONDS` | `900` | 到预约开始后多少秒仍未签到才算 No-Show |
| `EARLY_ARRIVAL_SECONDS` | `900` | 允许提前签到的秒数 |
| `MIN_PLATE_CONFIDENCE` | `0.7` | 自动匹配车牌所需最低置信度 |
| `EVENT_PAGE_SIZE` | `100` | 单次事件轮询最大条数 |
| `LOG_LEVEL` | `INFO` | 应用日志级别；应用日志不记录车牌或 Token |

公开和 Mock 接口均要求：

```http
Authorization: Bearer local-dev-token
```

`POST /v1/reservations` 还必须包含非空的 `Idempotency-Key`。同一键和同一请求会返回原预约；同一键搭配不同请求返回 409。

## 接口目录

公开契约（YAML 的 server URL 已包含 `/v1`，因此本地完整路径如下）：

| 方法与路径 | 用途 |
|---|---|
| `GET /v1/bays` | 查询测试车位 |
| `GET /v1/bays/{bay_id}/status` | 分别查询物理占用、保护设备、当前预约 |
| `POST /v1/reservations` | 创建预约；支持幂等与并发冲突仲裁 |
| `GET /v1/reservations/{reservation_id}` | 查询公开预约状态 |
| `POST /v1/reservations/{reservation_id}/release` | 安全地结束预约资源占用 |
| `GET /v1/events?after=cursor-00000001` | 稳定、非破坏性地轮询事件 |

开发专用接口（不是原始 OpenAPI、也不是 Ecurious 官方能力）：

| 方法与路径 | 用途 |
|---|---|
| `POST /mock/events/plate` | 模拟车牌识别与签到；`source_event_id`/`event_id` 可用于去重 |
| `POST /mock/bays/{bay_id}/occupancy` | 独立模拟车辆进入、离开或未知占用状态 |
| `POST /mock/reservations/{id}/charging/start` | 模拟开始充电 |
| `POST /mock/reservations/{id}/charging/stop` | 模拟结束充电 |
| `POST /mock/no-show/run` | 立即执行一次可控时间的 No-Show 扫描 |
| `POST /mock/reset` | 清除预约和事件，并恢复三个初始车位 |

## 可复制的基本 cURL 示例

先设置公共参数：

```bash
BASE=http://127.0.0.1:8000
TOKEN=local-dev-token
```

查询车位与状态：

```bash
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/v1/bays"
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/v1/bays/BAY-001/status"
```

创建预约。以下时间由 Python 生成，确保处于可签到窗口：

```bash
START=$(python -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat())')
END=$(python -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())')

CREATE_RESPONSE=$(curl -sS -X POST "$BASE/v1/reservations" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: demo-booking-001" \
  -H "Content-Type: application/json" \
  -d "{\"bay_id\":\"BAY-001\",\"license_plate\":\"M-AB1234\",\"start_time\":\"$START\",\"end_time\":\"$END\"}")

echo "$CREATE_RESPONSE"
RESERVATION_ID=$(python -c 'import json,sys; print(json.loads(sys.argv[1])["reservation_id"])' "$CREATE_RESPONSE")
```

模拟正确车牌签到、车辆进入、充电、释放和离开：

```bash
curl -sS -X POST "$BASE/mock/events/plate" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"bay_id":"BAY-001","license_plate":"M-AB1234","confidence":0.97,"source_event_id":"camera-demo-001"}'

curl -sS -X POST "$BASE/mock/bays/BAY-001/occupancy" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"occupancy":"occupied","license_plate":"M-AB1234"}'

curl -sS -X POST "$BASE/mock/reservations/$RESERVATION_ID/charging/start" \
  -H "Authorization: Bearer $TOKEN"
curl -sS -X POST "$BASE/mock/reservations/$RESERVATION_ID/charging/stop" \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X POST "$BASE/v1/reservations/$RESERVATION_ID/release" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"reason":"charging_completed"}'

curl -sS -X POST "$BASE/mock/bays/BAY-001/occupancy" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"occupancy":"free"}'
```

错误车牌不会签到或授权释放保护设备：

```bash
curl -sS -X POST "$BASE/mock/events/plate" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"bay_id":"BAY-001","license_plate":"N-CD5678","confidence":0.98,"source_event_id":"camera-wrong-001"}'
```

轮询与续读事件：

```bash
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/v1/events"
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/v1/events?after=cursor-00000001"
```

模拟 No-Show 时无需真实等待。为某个过去开始且仍为 `reserved` 的预约传入未来模拟时间即可：

```bash
SIMULATED_NOW=$(python -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(hours=2)).isoformat())')
curl -sS -X POST "$BASE/mock/no-show/run" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"now\":\"$SIMULATED_NOW\"}"
```

重置环境：

```bash
curl -sS -X POST "$BASE/mock/reset" -H "Authorization: Bearer $TOKEN"
```

## 状态与安全语义

内部状态机为：

```text
RESERVED -> CHECKED_IN -> CHARGING -> CHARGE_COMPLETED -> RELEASED
    |                                      
    +-> CANCELLED / EXPIRED / FAILED
```

公开 YAML 没有 `charging`、`completed` 或 `failed` 值，因此内部 `CHARGING` 和 `CHARGE_COMPLETED` 对外仍映射为 `checked_in`；最终安全释放后映射为 `released`。

系统独立保存三种事实：预约业务状态、物理占用状态、保护设施状态。释放预约不会把 `occupied` 强行改成 `free`，车辆占用时也不会模拟在车辆下方升起保护设施。未签到车辆占据已预约车位时，Mock 返回 `blocked=true`，保留真实 `occupied` 状态并写入 `occupancy.changed` 事件。

## SQLite 与并发

SQLite 表为 `bays`、`reservations`、`events`。数据库自动初始化并持久化到 `DATABASE_URL`。预约时间和事件时间统一转换为带 UTC 时区的 ISO 8601 字符串。

创建预约使用 `BEGIN IMMEDIATE` 事务；重叠判断和插入处于同一个写事务中，因此同一车位同一时段的并发请求只有一个能成功。数据库同时对 `idempotency_key` 和非空 `source_event_id` 建立唯一约束。

事件游标是持久化自增序号的文本表示 `cursor-NNNNNNNN`。轮询只读、不删除事件；重复使用相同游标会得到稳定结果。

## 自动化测试

```bash
cd backend
source .venv/bin/activate
python -m pytest
```

测试数据库使用 Pytest 的临时目录，不会污染本地运行数据库。测试覆盖：车位查询、状态查询、预约创建/读取、重叠与并发冲突、幂等重放、释放与重复释放、正确/错误/重复车牌事件、No-Show、Blocked Bay、事件游标、持久化、异常参数、认证、完整充电生命周期、重置以及禁用 Mock 接口。

## 文档对比、假设与已知限制

两份原始文档的 6 个公开端点、字段、枚举和 Bearer Token 要求基本一致，没有直接字段冲突。实现以 YAML 为公开契约，并对文字说明作如下处理：

- YAML 的 `servers.url` 已带 `/v1`，所以 YAML 路径 `/bays` 在本服务中实现为 `/v1/bays`。
- YAML 全局 `security` 适用于所有公开操作；文字文档只在部分示例重复写认证头。本实现也保护所有 `/mock/*` 接口。
- YAML 未规定游标格式、分页上限和非法游标错误。本 Mock 自行采用 `cursor-<自增序号>`、`EVENT_PAGE_SIZE` 及 422。
- YAML 的事件枚举只有 `plate.detected`、`occupancy.changed`、`reservation.expired`。车牌不匹配、低置信度和 Blocked Bay 等异常作为内部 `outcome`/元数据保存，并由 Mock 接口返回；公开事件对象不增加 YAML 未定义字段。
- 原始公开 API 没有签到或充电接口。正确车牌在有效窗口内触发签到、最低置信度、提前到达窗口以及 `/mock/*` 充电流程，均是本项目自行设计的模拟行为。
- 创建预约时拒绝当前已物理占用、未知保护状态或 `reservation_supported=false` 的车位，这是 Mock 的保守策略。
- 创建预约后立即标记模拟保护状态为 `protected`；真实设备何时保护车位尚待现场确认。
- 重复 release 对已经终止的预约返回同一结果且不重复产生副作用；原规范没有定义这项重放语义。
- `release` 只结束预约资源与保护安排，不直接改变物理占用。真实硬件安全联锁、故障码和人工处置流程未知。
- Mock 不实现真实充电桩会话、功率/计费、图片存储、Webhook、WebSocket、OCPI/OCPP、事件保留清理或真实企业适配器。
- 示例车牌为合成数据；SQLite 仍保存车牌以完成匹配。用于更长期环境前应补充访问控制、加密和保留/删除策略。
- 官方 Base URL、认证生命周期、车位映射、硬件动作、正式事件机制及 GDPR 细节均必须在现场确认。
