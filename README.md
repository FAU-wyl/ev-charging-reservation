# Ecurious EV Charging Reservation System

这是一个面向司机和充电站运营方的全栈黑客马拉松原型，包含地图找桩、账号与车辆、受保护车位预约、车牌自动签到、模拟充电、No-Show、Blocked Bay、运营告警和商业分析。

> 重要：仓库中的 Ecurious 接口和硬件行为是赛事开发用的 **fictional Mock**，不是 Ecurious 官方 API。充电状态与收入数据也明确标记为 Simulation / Estimated。

## 架构与端口

```text
React + Vite :5173
        |
        | /api（司机端与管理员端只访问应用 API）
        v
FastAPI App Backend :8001 ---- SQLite ecurious_app.db
        |
        | EcuriousAdapter + server-side Bearer Token
        v
Fictional Ecurious Mock :8000 -- SQLite ecurious_mock.db
```

- `frontend/app/`：保留并升级原 Leaflet 地图；新增司机端与 CPO Admin Dashboard。
- `app_backend/`：用户认证、车辆、站点目录、应用预约、权限、事件同步、告警和分析。
- `backend/`：严格模拟 `openapi.yaml` 的企业预约/硬件边界及开发专用 `/mock/*`。
- `goingelectric_response.json`：已有合法开发响应 fixture。默认不在线调用 GoingElectric，也不把 connector `count` 当成实时空闲车位。

React 不直接调用企业 API 或 `/mock/*`；管理员 Demo 操作先到 App Backend，再由服务端 Adapter 调用 Mock。

## 一次安装

需要 Python 3.11+ 和 Node.js 20+。

```bash
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt -r app_backend/requirements.txt

cd frontend/app
npm ci
```

三个服务都直接读取进程环境变量，不会自动解析 `.env`。以下命令分别在三个终端运行。

## 启动三个服务

终端 1：Ecurious Mock Server（8000）

```bash
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack/backend
source ../.venv/bin/activate
set -a; source .env.example; set +a
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

终端 2：App Backend（8001）

```bash
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack/app_backend
source ../.venv/bin/activate
set -a; source .env.example; set +a
python -m uvicorn app_backend.main:app --host 127.0.0.1 --port 8001 --reload
```

终端 3：React（5173）

```bash
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack/frontend/app
npm run dev
```

访问地址：

- Web App：<http://127.0.0.1:5173>
- App Backend Swagger：<http://127.0.0.1:8001/docs>
- Ecurious Mock Swagger：<http://127.0.0.1:8000/docs>

## 本地 Demo 账号

只有 `APP_SEED_DEMO_USERS=true` 且两个密码均至少 8 位时才会创建 Demo 用户。`.env.example` 中的凭据仅限本机演示：

| 角色 | Email | 开发密码 |
|---|---|---|
| DRIVER | `driver@demo.local` | `DriverDemo123!` |
| ADMIN | `admin@demo.local` | `AdminDemo123!` |

注册页面只能创建 DRIVER。ADMIN 权限由后端数据库角色强制检查，不能靠隐藏前端按钮绕过。生产环境必须关闭 Demo seed 并使用独立强密码。

## 可重复 Demo

### 1. 正常预约闭环

1. 以 DRIVER 登录。
2. 打开地图，勾选 `Reservable only`，选择 `Parkhaus Elisenhof Hauptbahnhof`（`GE-26` / `BAY-001`）。
3. 若无车辆，在 `Vehicles` 添加合成测试车牌 `M-AB1234`。
4. 选择时间并 Reserve。页面只在 Mock Provider 返回成功后显示确认。
5. 另一个浏览器会话以 ADMIN 登录并打开 `/admin`。
6. `Demo control room` 依次执行：`Correct plate`、`Vehicle enters`、`Start charging`、`Stop charging`。
7. 在 `Reservations` 管理页释放预约，再执行 `Vehicle leaves`。
8. 司机预约详情每 5 秒自动刷新，最终显示 `COMPLETED`。

所有步骤都经过 HTTP API、两个 SQLite 数据库和事件同步；不是前端定时伪造状态。

### 2. 错误车牌与 Blocked Bay

1. 创建 `BAY-003` 的预约。
2. ADMIN 点击 `Wrong plate`：系统记录 `WRONG_PLATE`，司机看到 `ATTENTION_REQUIRED`，但不会自动认定物理占用。
3. ADMIN 再以错误车牌点击 `Vehicle enters`：系统读取真实 Mock occupancy，升级为 `BLOCKED` 并生成 `BLOCKED_BAY` 告警。

### 3. No-Show

1. 创建预约但不要模拟正确车牌到达。
2. ADMIN 点击 `Run No-Show`。App Backend 让 Mock 使用模拟未来时间执行规则，无需现场等待。
3. Mock 返回释放结果并产生 `reservation.expired`；事件同步后司机与 Admin 都显示 `EXPIRED`。

### 4. 重叠预约

用两个 DRIVER 对同一 Bay 和重叠时间创建预约。最终冲突由 Ecurious Mock 在事务中仲裁，第二个请求返回 `409 BAY_ALREADY_RESERVED`。

## 关键 API

App Backend：

```text
POST /api/auth/register        POST /api/auth/login
GET  /api/auth/me              POST /api/auth/logout
GET/POST/PATCH/DELETE /api/vehicles
GET  /api/stations             GET /api/bays/{id}/availability
POST /api/reservations         GET /api/reservations/my
GET  /api/reservations/{id}    POST /api/reservations/{id}/cancel
GET  /api/admin/overview       GET /api/admin/bays
GET  /api/admin/reservations   GET /api/admin/events
GET  /api/admin/alerts         GET /api/admin/analytics
POST /api/admin/demo/*         # ADMIN-only, Mock-only
```

Mock Server 公开契约仍为 `/v1/bays`、`/v1/reservations`、`/v1/events` 等，详见 `backend/README.md`。

## 数据与事件同步

- App DB 分开保存 `users`、`sessions`、`vehicles`、`stations`、`bay_mappings`、`app_reservations`、`processed_events`、`alerts` 和 `sync_state`。
- 本地 App Reservation ID 和 Provider Reservation ID 分开保存。
- 密码使用带随机 salt 的 `scrypt`；登录返回随机 opaque token，数据库只保存 token 的 SHA-256 摘要，logout 会撤销会话。
- App Backend 按 `APP_EVENT_POLL_SECONDS` 轮询 Provider。cursor、event ID 和处理结果持久化。
- SQLite lease 防止 reload/multi-worker 同时重复消费；数据库唯一约束再次防止事件重复副作用。
- Admin 事件列表只返回掩码车牌，例如 `M-***34`。
- 司机只能访问自己的预约；所有 ADMIN API 都在后端校验角色。

## GoingElectric 与密钥

- 当前 UI 使用已有 `goingelectric_response.json`，数据源显示为 `GoingElectric fixture` 并保留原站点链接。
- 普通站点显示 `Availability Unknown`；只有 `bay_mappings` 中明确连接的站点出现预约能力。
- `count` 只显示为已安装 connector 数量，不推断空闲数量。
- 原测试脚本中的硬编码 Key 已移除，现在只读取 `GOINGELECTRIC_API_KEY` 环境变量。
- `frontend/app/.env` 和所有 `.env` 均被 `.gitignore` 忽略。不要使用 `VITE_*` 保存 GoingElectric 或 Ecurious 密钥，因为浏览器可读取所有 Vite 客户端变量。
- 如果曾经共享过旧硬编码 Key，应在提供方后台轮换它。

## 自动化验证

Mock Server：

```bash
cd backend
../.venv/bin/python -m pytest
```

App Backend：

```bash
cd app_backend
../.venv/bin/python -m pytest
```

React：

```bash
cd frontend/app
npm run lint
npm test
npm run build
```

当前实际结果：

```text
Mock Server: 18 passed
App Backend: 13 passed
Frontend:    5 passed
Oxlint:      0 warnings
Vite build:  successful
Browser:     desktop + 390px mobile, no console errors
Real 3-service demo: COMPLETED / BLOCKED / EXPIRED, 7 events, 2 alert types
```

测试覆盖认证、权限隔离、车辆、预约、幂等、冲突、取消、超时/服务故障、正确与错误车牌、Blocked Bay、No-Show、事件去重、管理 KPI、完整充电模拟和持久化。

## Mock 边界和下一步

目前仍是 Mock 或尚未连接的部分：

- 真实 Ecurious Base URL、认证、字段、签到、事件和硬件协议未知。
- `CHARGING` / `CHARGE_COMPLETED` 是赛事演示信号，不是真实充电桩遥测。
- 收费和收入是环境变量驱动的 Simulation / Estimated，不是财务数据。
- GoingElectric 使用静态开发 fixture；上线前必须确认许可、缓存及代理规则。
- 尚未实现邮件验证、密码找回、MFA、生产审计/速率限制和高可用数据库。
- 当前是 polling；若官方提供签名 Webhook/WebSocket，可替换同步入口。

拿到官方 API 后主要修改：

1. 在 `app_backend/app_backend/adapters/` 实现正式 Adapter。
2. 在 `app_backend/config.py` 增加正式 Base URL 和凭据配置。
3. 根据官方枚举调整 `services/reservations.py` 的状态映射。
4. 根据官方事件机制替换或扩展 `services/event_sync.py`，保留本地去重和游标处理。
5. 更新 `bay_mappings`，不需要修改 React 的用户、车辆、预约或 Admin 页面。

