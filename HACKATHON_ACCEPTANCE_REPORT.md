# ECURIOUS Hackathon Mandatory Requirements 验收报告

## 2026-10-24 P0/P1 修复回归结论（当前结果）

本节是六项软件缺陷修复后的最新结论；后面的“修复前基线”保留第一轮验收证据，
其中的 P0/P1 复现结果不再代表当前代码。

### 修复结果

| Issue | Status | 主要实现 | 回归证据 |
|---|---|---|---|
| P0 Release Consistency | **FIXED AND TESTED** | `RELEASE_PENDING/FAILED/UNKNOWN`、`release_operations` 审计、SQLite lease、物理空闲检查、全量有效授权同步、幂等恢复 | 成功、失败、超时、未知结果、数据库提交失败、重复、并发、占用中、多预约、临时失败恢复 |
| P1 No-Show Grace | **FIXED AND TESTED** | Live 使用 `start_time + grace`；UTC aware；到期前读取设备；未知/占用转人工复核 | 未到期、精确边界、超过、已签到、已取消、重复、时区、Offline/unknown/occupied、撤权失败 |
| P1 SSE Ordering | **FIXED AND TESTED** | 每 Lock、每事件类型独立持久水位线；先声明事件再执行业务；旧/相同/无效时间不覆盖 | 正序、逆序、重复、相同时间、缺失/错误时间、多 Lock、并发 Worker、重连快照 |
| P1 Mock/Live Check-in | **FIXED AND TESTED** | 统一为正确车牌 + 同车位 5 分钟内 occupied，顺序不限；车牌单独只进入等待 | plate-only、occupancy-only、两种顺序、错牌、过期、错 Lock、重复、Manual fallback、冲突 |
| P1 Charging Session | **FIXED AND TESTED** | 持久化 Session、服务端计时、配置费率/费用、DRIVER 权限、前端 timer、Analytics | Start/Stop、时长/费用、重复、未登录/越权/非法、重启持久化、充电中 Release |
| P1 Hardware ID | **FIXED AND TESTED** | Live 默认/示例 `lock-0001`；Mock ID 按 mode 隔离；不把设备序列号当 path ID | Live/Mock 映射、无重复真实 Bay、Admin/SSE/预约映射、未知 ID |

### 最新自动化与构建

```text
Mock Backend: 19 passed
App Backend:  55 passed
Frontend:      6 passed
Total:        80 passed, 0 failed
Oxlint:       0 warnings
Vite:         81 modules, build successful
Python:       compileall, both FastAPI imports successful
```

相对第一轮 `54 passed`，新增 **26** 项回归测试；依赖错误行为的旧测试已改为
“车牌只验证身份、占用才完成签到”的正确断言。

隔离进程 Mock E2E 实际结果：

```text
GoingElectric stations=178
plate only=RESERVED
plate + occupied=CHECKED_IN
charging session=ACTIVE -> COMPLETED (server duration persisted)
vehicle departure=free
release=COMPLETED
duplicate release=COMPLETED
next reservation=RESERVED
```

浏览器回归通过地图加载、DRIVER/ADMIN 登录、预约详情、Charging Session ID、
开始/结束时间、时长、配置费率、Estimated Cost、完成状态和 Admin Analytics；
浏览器控制台无 error。真实 ECURIOUS 仅执行只读检查：

```text
lock-0001: online=true, barrier=moving, occupancy=free
last_seen=2026-10-24T11:26:09.207Z
```

没有发送真实 unlock、lock、whitelist 或 keep-state。团队此前人工确认的实体升降
PASS 保留，但本轮没有重复执行。

### 当前 Mandatory Requirements

| Requirement | Current Result | 结论 |
|---|---|---|
| GoingElectric Data | **PASS** | 178 条真实来源 fixture 与地图/API 保持通过 |
| Full Reservation Cycle | **PARTIAL** | Mock 全闭环 PASS、Live 软件缺陷已修复；真实 EEPROM/LPR/SSE/完整预约仍未现场闭环 |
| Technical Documentation | **PASS** | README、App/Frontend README、环境示例、API 与本报告已同步 |
| At Least Two Mission Areas | **PASS** | No-Show、Blocked Bay 可运行，LPR/Charging Simulation 亦有回归 |
| Limitations and Scalability | **PASS** | 继续明确 SQLite、SSE 无 replay、静态 fixture、隐私和真实计费限制 |

当前结论：**Mock Demo Ready；Live Backend Integration Ready for a controlled
manual test；Live Hardware Full Demo 仍需现场验证，不能标记为 PASS。**

### 仍余风险

- 真实 EEPROM 白名单新增/撤销没有在本轮自动执行，需现场逐次确认。
- 真实 `photo_captured` + `vehicle_presence` SSE 配对和断线窗口尚未完成实体闭环。
- 欧洲车牌 OCR 仍是已知失败点；中国车牌只读结果成功不等于完整自动签到 PASS。
- 官方 SSE 没有 replay/可靠 sequence；当前以每类时间水位线和重连快照保守处理，
  无法恢复服务端从未送达的事件。
- Charging 全部明确为 Simulation / Estimated，没有真实功率、电量、支付或发票。
- SQLite + 本地 lease 适合 Hackathon 单实例，不替代生产级数据库和分布式锁。

---

# 修复前基线（历史记录）

验收日期：2026-10-24  
验收角色：Senior QA / Full-Stack Integration / Technical Review  
工作分支：`main`  
基线提交：`c51f0d6 Initial commit`

## 0. 结论摘要

- 五项 Mandatory Requirements：**3 项 PASS，2 项 PARTIAL**。
- 自动化测试：**54 passed，0 failed**（Mock Backend 18、App Backend 31、Frontend 5）。
- Python syntax/import、SQLite 初始化、前端 lint/build 均通过。
- GoingElectric：使用 178 条静态 fixture；抽查的公开站点名称、地址、坐标、接口数量和功率与 GoingElectric 页面一致。它不是实时状态数据。
- Mock Reservation E2E：登录、车辆、预约、冲突、LPR、占用、Charging Simulation、离场、释放、二次预约均实际运行通过。
- Physical barrier raise/lower：**PASS（团队现场人工验证，本次未重新执行）**。
- 真实 ECURIOUS REST：只读连接成功；真实 SSE 握手成功；本次锁状态为 Offline。
- 真实 LPR 签到、真实白名单 EEPROM、真实 SSE 事件处理及完整 Live Reservation E2E：未完成现场闭环，不能判定为 PASS。
- 当前存在 **1 个 P0**：Live Release 在白名单撤销失败后仍把预约保存为 `COMPLETED`。
- 当前结论：**Mock Demo Ready；Live Hardware Full Demo 尚未 Ready**。

## 0.1 安全与 Git 约束执行

开始时实际执行：

```text
git status --short --branch
git branch --show-current
git log -1 --oneline
```

结果：当前为 `main`，基线 `c51f0d6`。验收开始前已有用户修改
`app_backend/scripts/create_admin.py`，本次未覆盖该文件。

本次未执行 `git pull`、`push`、`merge`、`rebase`、`reset` 或分支切换；未提交代码；未输出 API Key；未调用真实硬件的 Unlock、Lock、Whitelist 或 Keep-State。

---

# Part A — Five Mandatory Requirements

| Requirement | Status | Evidence | Problems |
|---|---|---|---|
| 1. GoingElectric Data | **PASS** | 178 条 fixture；Station API 200；浏览器地图、Marker、搜索、筛选、详情和预约入口通过；抽查 GE #26 / #484 与公开页面一致 | 静态数据，不代表实时可用状态；来源导出时间未写入 fixture |
| 2. Full Reservation Cycle | **PARTIAL** | Physical Unlock/Lock 团队人工 PASS；真实 REST 与 SSE 握手通过；Mock E2E 全流程通过 | 真实 LPR、真实白名单、真实 SSE 事件和真实完整闭环未验证；Live Release 存在 P0 |
| 3. Technical Documentation | **PARTIAL** | README 已覆盖架构、Mock/Live、安全规则、API、启动和测试；本报告补充完整验收、限制和扩展性 | 根 README 的 `bay-1` 示例与现场 `lock-0001` 不一致；缺少集中数据流/DB/实际故障记录；未记录团队升降 PASS 和欧洲车牌失败 |
| 4. At Least Two Mission Areas | **PASS** | No-Show 与 Blocked Bay 均在 Mock 真实进程 E2E 中运行通过 | Live No-Show 计时错误；Blocked Bay 没有替代车位推荐 |
| 5. Limitations and Scalability | **PASS** | 本报告 Part A.5 明确区分当前实现、架构潜力和未来计划 | 根 README 原先没有完整集中章节，应在提交前链接本报告 |

## A.1 Requirement 1 — GoingElectric Charging Station Integration

### 数据来源结论

`goingelectric_response.json` 是 GoingElectric 格式的静态响应，顶层为
`status=ok` 和 `chargelocations`，共有 **178** 条记录。数据库在
`app_backend/app_backend/database.py:225-305` 中读取该文件并保存：

- `ge_id`
- 名称、城市、国家、邮编、街道
- 经纬度
- 网络
- connector 类型、功率和数量
- `verified` / `fault_report`
- 原 GoingElectric URL

公开来源抽查：

- [GoingElectric — Parkhaus Elisenhof Hauptbahnhof #26](https://www.goingelectric.de/stromtankstellen/Deutschland/Muenchen/Parkhaus-Elisenhof-Hauptbahnhof-Luitpoldstrasse-3/26/)
- [GoingElectric — Parkgarage Rieger City Ladesäule #484](https://www.goingelectric.de/stromtankstellen/Deutschland/Muenchen/Parkgarage-Rieger-City-Ladesaeule-Frauenstrasse-38/484/)

两页公开信息与 fixture 的名称、地址、坐标和 `4 × Type 2 / 11 kW` 一致。因此数据不是虚构站点，但 fixture 本身没有导出时间，且不得解释为实时空闲状态。

### 实际测试结果

- `GET /api/stations`：HTTP 200，178 条。
- 浏览器地图初始实际显示：当前地图视野 121 个站点、2 个可预约站点（地图移动后视野计数会变化）。
- Leaflet 地图与 OpenStreetMap tile 正常渲染。
- Marker 点击可显示名称、地址、connector、网络、目录状态和 GoingElectric 原链接。
- `Reservable only` 可将视图过滤到明确映射的站点。
- 搜索 `Elisenhof` 返回 1 条匹配站点。
- 未映射站点显示 `Availability Unknown`，不会错误显示为可预约。
- 站点详情可进入预约入口；未登录时要求登录。
- 390 × 844 移动端详情页布局正常。
- 浏览器控制台无 error/warn。

前端相关代码：

- `frontend/app/src/pages/MapPage.jsx`
- `frontend/app/src/pages/StationPage.jsx`
- `frontend/app/src/api.js`

后端相关代码：

- `app_backend/app_backend/database.py:225-305`
- `app_backend/app_backend/services/stations.py`
- `app_backend/app_backend/api/stations.py`

### 映射规则

只有 `bay_mappings` 中的站点可以预约。Mock 使用 `BAY-001/002/003`；Live 会把配置的 `ECURIOUS_LOCK_ID` 映射到第一条 fixture 站点。Live 实际 API 返回：

```text
station_count=178
reservable_station_count=1
GE-26 -> BAY-001, lock-0001
lock-0001 availability -> Hardware Offline
```

保留的 `BAY-001` 是旧 Mock 映射，Live provider 不支持它；详见 P2-3。

## A.2 Requirement 2 — Complete Reservation Lifecycle

### A. Physical barrier raise/lower

```text
Physical barrier raise/lower: PASS (manually verified by team)
```

这是团队提供的现场人工结果。本次验收没有重新发送实体升降命令。

### B. Real API read-only connectivity

实际只读结果：

```text
GET /api/v1/locks                         PASS
GET /api/v1/locks/lock-0001               PASS
GET /api/v1/locks/lock-0001/plate/latest  PASS
GET /events                               HTTP 200, text/event-stream
```

账号当前可列出 `lock-0001`、`lock-0002`、`lock-0003`；当前配置指向
`lock-0001`。文档中的 `bay-1` 请求实际返回 Access Denied，因此不能继续把
`bay-1` 当作团队设备 ID。

验收时 `lock-0001` 状态：

```text
hardware_online=false
barrier_status=offline
occupancy=free
active_errors=[]
```

最近车牌 GET 返回成功，报告仅记录掩码 `京A***01`。这证明只读 LPR 结果可以获取，但不证明 LPR 已完成预约签到。

### C. Reservation lifecycle in Mock E2E

使用三个真实进程（Mock :18000、App Backend :18001、Vite :15173）和独立临时 SQLite 实际完成：

```text
Driver Login
  -> Create Vehicle
  -> GET GoingElectric Station
  -> Create Reservation
  -> Idempotent Replay
  -> Overlap Rejected (409 BAY_ALREADY_RESERVED)
  -> Simulate Correct Plate
  -> Simulate Occupied
  -> Start Charging
  -> Stop Charging
  -> Simulate Free
  -> Admin Release
  -> Duplicate Release
  -> Next Driver Reservation
```

主要状态结果：

```text
RESERVED
CHECKED_IN
CHARGING
CHARGE_COMPLETED
COMPLETED
```

释放后 `occupancy=free`、`active_reservation_id=null`；下一名用户可以预约。重复 Release 保持 `COMPLETED`，没有重复副作用。

### D. Real LPR check-in

**NOT VERIFIED**。

- 真实 `/plate/latest` 可读到一次中国车牌结果。
- 团队已知欧洲车牌识别失败，不能假设已修复。
- 本次没有真实预约 + 真实 `photo_captured` + 真实 `vehicle_presence` 的完整配对事件。
- Live 代码会等待占用事件后才签到；进程内 Fake Adapter 测试通过。
- 有效预约用户的 Manual Unlock fallback、权限限制和审计已有自动化测试，但未在本次实体车位重新执行。

### E. Full integrated hardware reservation lifecycle

**PARTIAL / MANUAL CHECK REQUIRED**。

已验证：

- App 可以加载 Live Adapter。
- Base URL、API Key 环境加载、`X-API-Key`、REST schema 正常。
- Live/Mock 可切换且 Live 故障不会回退 Mock。
- `GE-26 -> lock-0001` Live 映射由实际 App API 返回。
- SSE HTTP 握手成功。
- Unlock / Lock 实体动作由团队人工通过。

未验证：

- 创建真实预约后 EEPROM 是否实际写入正确白名单。
- 取消/No-Show/Release 后 EEPROM 是否实际删除授权。
- 真实 `photo_captured` 与 `vehicle_presence` 是否由 SSE 到达并完成签到。
- 真实 LPR -> Charging Simulation -> Release 的完整单次闭环。

因此不能把物理升降 PASS 等同为 Requirement 2 全部 PASS。

### 2.2 Reserve / Whitelist

自动化（Fake Adapter / MockTransport）验证通过：

- Live 预约保存在 App SQLite，不依赖不存在的官方 reservation API。
- 同车位重叠时间返回 409。
- 白名单只取当前授权窗口内的车牌。
- 车牌规范化和去重。
- 最多 10 个车牌，超限拒绝。
- 同步失败返回错误并显示 `ATTENTION_REQUIRED` / `FAILED`。
- 取消和 No-Show 会请求撤销授权。
- Manual Unlock 只允许有效预约用户或 ADMIN，并写 `hardware_commands`。

真实 EEPROM 写操作按约束未执行，所以结果是 **PARTIAL**。

### 2.3 Arrive / SSE

代码与自动化覆盖：

- `status_change`
- `vehicle_presence`
- `photo_captured`
- `alarm`
- SSE parser、多行数据、异常时间戳
- 稳定 event ID 与数据库唯一约束去重
- 断线指数退避重连
- FastAPI 退出取消后台 listener 并关闭 HTTP client
- Live LPR 匹配后等待真实 `occupied` 才签到
- Manual Unlock + 5 分钟内 `occupied` fallback 签到
- 错误车牌 + 占用 -> `BLOCKED`

真实 SSE 只完成 HTTP 握手，未触发现场事件；并且专项测试发现乱序事件覆盖问题（P1-2）。

### 2.4 Charge

状态流转实际通过：

```text
CHECKED_IN -> CHARGING -> CHARGE_COMPLETED
```

前端明确显示 `Simulation / active|inactive`，Admin Analytics 明确显示
`Simulation / Estimated`，没有编造功率或电量。

但是当前没有独立 Charging Session、开始/结束时间、充电时长或单次费用记录；收入只按完成预约数和 No-Show 数乘固定假设。因此该子项是 **PARTIAL**。

### 2.5 Release

Mock 流程通过：车辆离开后释放、重复释放、车位恢复和下一用户预约均成功。Blocked Bay 即使先释放预约，物理 `occupancy` 仍保持 `occupied`，新预约返回 `409 BAY_OCCUPIED`；收到 `free` 后才可再次预约。

Live 存在 P0：白名单撤销失败时接口返回 503，但数据库预约已经是
`COMPLETED`。该行为违反“硬件通信失败时数据库不能声称完整释放成功”。

## A.3 Requirement 3 — Technical Documentation

### 已覆盖

- React / FastAPI / SQLite / Mock / Live 架构。
- Mock 与官方 API 的明确边界。
- REST/SSE 路径、认证方式和 Key 安全。
- macOS/Linux、Windows、Mock、Live 启动命令。
- 自动化测试命令和当前测试数量。
- 预约安全、白名单容量、并发 lease、事件去重。
- Simulation / Estimated 标识。
- 手动硬件测试脚本及逐次确认保护。
- Mock OpenAPI/YAML 与 Mock README。

### 缺失或过时

1. `README.md:94-100` 和 `.env.example` 示例仍写 `bay-1`，现场实际是
   `lock-0001`。
2. 未在提交文档中记录 `Physical Unlock/Lock PASS — team manually verified`。
3. 未明确记录“欧洲车牌失败、中国车牌可以识别”的现场事实。
4. App SQLite 的完整表关系、数据流、失败补偿策略没有集中图示。
5. Root README 没有完整的 Limitations & Scalability 章节。
6. Admin UI 在 Live 模式仍硬编码显示 `Mock provider connected`。
7. `frontend/app/test_goingElectric.py` 是独立手工脚本，不属于 npm/pytest 自动化入口，且未记录 fixture 的生成时间。

### 使用的工具与依赖

- Python 3.13、FastAPI、Uvicorn、Pydantic、httpx、pytest、SQLite。
- React、Vite、React Router、Leaflet、Vitest、Oxlint。
- GoingElectric 静态 fixture、OpenStreetMap tiles。
- ECURIOUS 官方 REST/SSE。
- 浏览器 DOM、桌面/390px 渲染和控制台检查。

### 实际遇到但未完全解决

- ECURIOUS API Key 前缀区分大小写，错误前缀曾导致 401；已修正本地配置，报告不保存 Key。
- 设备心跳曾在线，本次验收时为 Offline。
- 欧洲车牌 LPR 失败；最近一次只读结果为中国车牌。
- 完整真实 Reservation Lifecycle 尚未现场执行。
- 本报告定位的 P0/P1 见 Part D。

## A.4 Requirement 4 — Mission Areas

| Mission Area | Status | Evidence / Limitation |
|---|---|---|
| No-Shows | **Mock PASS / Live FAIL** | Mock 15 分钟 grace、释放、罚款、事件、反向测试均通过；Live 错误等到 end_time 才过期 |
| Blocked Bays | **Mock PASS** | 错车牌 + occupied -> BLOCKED + 告警；重复占用不重复建相同告警；释放后仍 occupied 会阻止新预约；free 后恢复 |
| License Plate Recognition | **PARTIAL** | Mock 匹配、拒绝和去重通过；真实最近车牌可读；欧洲车牌失败且真实签到未验证 |
| Interoperability | **PARTIAL** | Adapter 隔离 Mock/Live，React API 不变；未实现 OCPI/OCPP 和多运营商 |
| New Use Cases | **PARTIAL** | 受保护车位预约、Manual Unlock fallback、Blocked alerts 已实现；尚非生产级 |
| Utilisation | **PARTIAL** | Dashboard 有预约数、签到率、No-Show、Blocked、reserved hours；非真实充电利用率 |
| Business Case | **PARTIAL** | 固定预约费/No-Show 费的 Estimated KPI；无真实账单、能耗或支付 |

明确回答：**是，目前至少有两个可以实际运行并演示的 Mission Areas：No-Shows 和 Blocked Bays（均为 Mock 演示）。**

### No-Show 专项结果

- 未到场预约：`EXPIRED`，Mock 释放车位。
- 15 分钟 grace：Mock 使用 `NO_SHOW_GRACE_SECONDS=900`。
- 白名单撤销：Fake Live Adapter 测试通过，真实 EEPROM 未写。
- 模拟罚款：Analytics 按 `€5.00 × EXPIRED` 计算并标记 Estimated。
- 后续用户可以重新预约。
- 已签到用户运行 No-Show：保持 `CHECKED_IN`，扫描 count=0。
- 已取消用户运行 No-Show：保持 `CANCELLED`，扫描 count=0。
- 重复扫描：No-Show 总数和模拟费用不重复增长。

### Blocked Bay 专项结果

- 错误车牌后：`ATTENTION_REQUIRED`。
- 随后 `occupied`：`BLOCKED`，产生 `WRONG_PLATE` 和 `BLOCKED_BAY` 告警。
- 重复 occupied：同一场景 `BLOCKED_BAY` 告警数保持 1。
- 释放预约但仍 occupied：物理状态保持 occupied，新预约返回
  `409 BAY_OCCUPIED`。
- 收到 free：Blocked 告警被解决，后续预约成功。
- 系统未实现自动替代车位建议；真实团队只有一个主要测试 Bay，不能假设存在可替代 Bay。

## A.5 Requirement 5 — Limitations & Scalability

### 当前已实现

- 单个配置的 Live Lock 进入应用预约流程。
- 多个 GoingElectric 静态站点和明确 Bay 映射。
- SQLite `BEGIN IMMEDIATE` 预约冲突仲裁。
- 进程锁 + SQLite lease 串行化硬件写入。
- REST 错误映射、命令审计、SSE 重连和事件去重。
- 本地用户角色、session token hash、scrypt 密码。

### 当前限制

1. 现场主要验证一个真实锁，未完成多锁业务闭环。
2. 欧洲车牌识别存在兼容性问题。
3. EEPROM 白名单最多 10 个车牌。
4. 官方硬件接口不提供充电桩功率、能量、会话和计费数据。
5. Charging 与收入均为 Simulation / Estimated。
6. GoingElectric 使用静态 fixture，不是实时可用性。
7. 外部 REST/SSE、4G、心跳和 API rate limit 都是可用性依赖。
8. SQLite 不适合高并发、多节点写入；当前 lease 不能替代分布式锁。
9. SSE 无 replay cursor，断线窗口中的事件可能丢失。
10. 乱序事件没有版本/时间保护。
11. 车牌属于个人数据；数据库明文保存，尚无加密、保留/删除、DSAR 策略。
12. 没有多 CPO tenant、站点级权限、OAuth/OIDC、MFA 或 API rate limiting。
13. 没有真实支付、发票、真实电量和电价。
14. 没有 OCPI / OCPP。

### 架构具备的扩展能力

- `EcuriousAdapter` 可以增加其他停车锁运营商实现，而保持 React API 不变。
- `bay_mappings` 可以扩展到多个站点和锁。
- 独立的 EventSync / HardwareService 可迁移到后台 worker。
- 统一内部状态和事件表可作为消息队列消费者的落库层。
- GoingElectric StationService 可以替换 fixture loader，而无需改预约 API。

### 未来计划（尚未实现）

1. PostgreSQL + 唯一/排他时间约束，替换 SQLite。
2. Redis/数据库 advisory lock，支持多 worker、多实例。
3. Kafka/RabbitMQ/SQS + transactional outbox，可靠分布式事件处理。
4. 每个设备维护 monotonic sequence/version，拒绝乱序旧事件。
5. 多 CPO tenant、Operator/Location/EVSE/Connector 标准模型和租户权限。
6. OCPI 对接第三方 EV App、位置和预约；OCPP 对接充电会话和真实 meter values。
7. OAuth2/OIDC、短期 scoped service token、mTLS、rate limiting 和完整 audit。
8. 多城市、多国家、时区、语言、车牌格式和数据驻留策略。
9. 加密车牌、可配置保留期、访问审计、删除与导出流程，完成 GDPR DPIA。
10. SSE/Webhook gateway、事件 replay、dead-letter queue、告警和可观测性。
11. 真实 Charging Session、功率/电量/时长/费率/支付/发票模型。

---

# Part B — Hardware & Integration Status

| Test | Result |
|---|---|
| Physical Unlock | **PASS — team manually verified** |
| Physical Lock | **PASS — team manually verified** |
| Live API Connectivity | **PASS (read-only)** — REST 200；SSE 200 `text/event-stream`；锁本次 Offline |
| Reservation → Hardware Mapping | **PARTIAL** — Live API 返回 `GE-26 -> lock-0001`，但未创建真实预约 |
| License Plate Whitelist Integration | **PARTIAL** — MockTransport/Fake Adapter 通过；真实 EEPROM 未写 |
| Real LPR Check-in | **NOT VERIFIED** — 中国车牌最近结果可读，欧洲车牌已知失败，无真实签到闭环 |
| SSE Real-time Events | **PARTIAL** — 真实握手成功；Mock/单测通过；未收到本次现场实体事件，且有乱序覆盖 Bug |
| Charging Simulation | **PARTIAL** — 状态和 UI 通过，无 Charging Session/时长/单次费用 |
| Release Workflow | **FAIL (Live)** — Mock 通过；Live 白名单撤销失败仍保存 `COMPLETED` |
| Full Reservation E2E | **PARTIAL** — Mock E2E PASS；真实硬件完整闭环未验证 |

Part B 计数：**PASS 3，PARTIAL 5，FAIL 1，NOT VERIFIED 1**。

---

# Part C — Test Execution

## C.1 执行命令与结果

### Git

```bash
git status --short --branch
git branch --show-current
git log -1 --oneline
git diff --check
```

结果：Git 状态可读，未执行同步或破坏性命令；验收前已有一处用户修改被保留。

### Python syntax / import

```bash
.venv/bin/python -m compileall -q backend/app app_backend/app_backend
cd backend && ../.venv/bin/python -c 'from app.main import app; print(app.title)'
cd app_backend && ../.venv/bin/python -c 'from app_backend.main import app; print(app.title)'
```

结果：PASS。

### Backend tests

```bash
cd backend
../.venv/bin/python -m pytest -q

cd ../app_backend
../.venv/bin/python -m pytest -q
```

结果：

```text
Mock Backend: 18 passed
App Backend:  31 passed
```

覆盖认证、权限、车辆 CRUD、预约幂等/冲突/取消、并发、Mock 完整生命周期、No-Show、Blocked、事件游标/去重、Live REST schema/error/timeout/offline、SSE reconnect、白名单容量、Manual Unlock、DB migration。

### Frontend

```bash
cd frontend/app
npm ci
npm run lint
npm test
npm run build
```

结果：

```text
npm audit: 0 vulnerabilities
Oxlint:     PASS
Vitest:     5 passed
Vite:       81 modules transformed, build successful
```

### 真实 ECURIOUS 只读

```bash
cd app_backend
set -a
source .env
set +a
python scripts/verify_live_readonly.py
```

另外执行只读 `/plate/latest` 和 `/events` 握手。所有请求为 GET，未输出 Key，未调用实体写操作。

### 浏览器验证

在独立 Mock 三服务环境测试：

- 地图、Leaflet、Marker、站点详情。
- `Reservable only`、搜索 `Elisenhof`。
- 可预约与不可预约文案。
- 站点详情和预约入口。
- `/admin` 未登录重定向 `/login`。
- 登录页表单。
- 桌面和 390px 移动端渲染。
- Console error/warn：0。

没有在浏览器自动输入任何真实或本地管理员密码；认证、车辆、预约和 Admin API 已由真实 HTTP 进程 E2E 与 pytest 覆盖。

## C.2 独立进程 E2E 数据证据

使用临时数据库，不污染现有数据库：

```text
stations=178
idempotent_replay=true
overlap_conflict=BAY_ALREADY_RESERVED
status_after_plate=CHECKED_IN (Mock，见 P1-3)
status_after_presence=CHECKED_IN
charging_start=CHARGING
charging_stop=CHARGE_COMPLETED
release=COMPLETED
duplicate_release=COMPLETED
next_reservation=RESERVED
no_show=EXPIRED
blocked_flow=ATTENTION_REQUIRED -> BLOCKED
duplicate_blocked_alert_count=1
```

No-Show 反向测试：

```text
checked-in user after scan = CHECKED_IN
cancelled user after scan = CANCELLED
scan expired count = 0 / 0
no-show total before/after = 1 / 1
```

Blocked Bay 安全测试：

```text
reservation status = BLOCKED
release while occupied keeps occupancy = occupied
next reservation while occupied = 409 BAY_OCCUPIED
next reservation after free = RESERVED
```

## C.3 Integration Chain

| Integration | Result |
|---|---|
| React → FastAPI | PASS（Vite + browser + HTTP） |
| FastAPI → SQLite | PASS |
| FastAPI → GoingElectric fixture | PASS |
| FastAPI → ECURIOUS Live REST | PASS（只读） |
| FastAPI → Mock Adapter | PASS |
| SSE → Event Handler | PARTIAL（真实握手；合成事件通过） |
| Event Handler → Reservation Service | PASS in tests/Mock；乱序保护 FAIL |
| Reservation Service → Database | PASS，Live Release failure consistency FAIL |
| Reservation Service → Hardware Adapter | PARTIAL（Fake/MockTransport；真实白名单未写） |
| Admin API → React Dashboard | API/build PASS；认证后 UI 未输入密码执行浏览器提交 |

## C.4 数量汇总

- 自动化测试：**54 passed / 0 failed / 0 skipped**。
- Part B 关键硬件与集成检查：**3 PASS / 5 PARTIAL / 1 FAIL / 1 NOT VERIFIED**。
- 剩余现场 Manual Checks：真实白名单、真实 LPR 签到、真实 SSE 事件、真实完整预约释放闭环。

---

# Part D — Bugs

## P0

### P0-1 Live 白名单撤销失败仍把预约标记为 COMPLETED

1. **描述**：ADMIN Release 先提交数据库 `COMPLETED`，再调用 whitelist sync。同步失败时 HTTP 返回 503，但数据库保持 `COMPLETED`。
2. **复现**：创建 Live 预约；让 `set_whitelist([])` 返回 503；调用
   `POST /api/admin/reservations/{id}/release`；再次 GET 预约。
3. **实际结果**：`release_http_status=503`、`reservation_status_after_failure=COMPLETED`、`hardware_sync_status=FAILED`。
4. **根因**：`app_backend/app_backend/services/reservations.py:279-294` 在外部调用前提交终态。
5. **建议**：引入 `RELEASE_PENDING` / `ATTENTION_REQUIRED`；先记录 intent，再幂等撤销白名单，确认成功后原子提交 `COMPLETED`；失败保持非终态并允许显式 retry/outbox。
6. **比赛影响**：直接违反 Release 要求，阻断 Full Reservation Lifecycle 的可靠 PASS。

## P1

### P1-1 Live No-Show 使用 end_time，而不是 start_time + grace

1. **描述**：Live 预约开始 30 分钟、grace=15 分钟且仍未签到时不会过期，只在预约结束后过期。
2. **复现**：start=now-30m、end=now+30m、status=RESERVED，调用 `_expire_due_locally()`。
3. **实际结果**：`expired_count=0`、状态仍 `RESERVED`。
4. **根因**：`app_backend/app_backend/services/hardware.py:353-373` 只比较 `end_time <= now`。
5. **建议**：比较 `start_time + reservation_grace_minutes <= now`，排除已签到/Charging/取消，并增加 start+grace 边界、重复任务、白名单撤销测试。
6. **比赛影响**：No-Show 在 Mock 可演示，但 Live 行为不符合方案。

### P1-2 乱序 SSE 旧事件会覆盖新硬件状态

1. **描述**：先处理新 `occupied`，再处理时间更早的 `free`，最终数据库变成 `free`。
2. **复现**：向 `process_event` 依次提交不同 event ID、时间倒序的 occupancy 事件。
3. **实际结果**：`stored_car_presence=free`。
4. **根因**：`app_backend/app_backend/services/event_sync.py:347-386` upsert 不比较事件时间/sequence，`updated_at` 使用处理时间。
5. **建议**：保存每类硬件状态的 `observed_at`/sequence，仅当新事件不旧于当前版本时更新；LPR、occupancy、barrier 分开维护时间戳。
6. **比赛影响**：断线重连或网络重排时可能错误签到、误判空闲或错误确认命令。

### P1-3 Mock LPR 单独触发 CHECKED_IN，与 Live/验收语义不一致

1. **描述**：Mock 正确车牌事件后、`vehicle_presence` 前预约已经是 `CHECKED_IN`。
2. **复现**：Mock 创建当前预约，调用 Correct Plate，仅查询预约。
3. **实际结果**：`status_after_plate=CHECKED_IN`。
4. **根因**：`app_backend/app_backend/services/event_sync.py:220-225` 仅 Live 等待 occupancy；Mock 从 provider 立即同步 checked_in。
5. **建议**：Mock 与 Live 统一为 `PLATE_MATCHED_WAITING_FOR_OCCUPANCY`，只有 matched plate/manual unlock + occupied 才签到。
6. **比赛影响**：Mock Demo 会演示与真实系统不同的关键到达语义。

### P1-4 Charging Simulation 缺少 Session、时长和单次费用

1. **描述**：只有预约状态和 `simulated_charging` 布尔值，没有 charging session 表、start/stop 时间、duration、energy、单次 fee。
2. **复现**：Start/Stop 后检查 API 与 SQLite。
3. **实际结果**：状态改变，但无法返回时长或单次费用；Analytics 只按预约数量乘固定假设。
4. **根因**：`app_backend/app_backend/api/admin.py:121-145` 仅更新预约；`services/admin.py:170-208` 聚合固定费率。
5. **建议**：新增明确标记 Simulation 的 `charging_sessions`，保存 start/end/duration、可配置假设费率；不伪造真实 kWh。
6. **比赛影响**：Requirement 2 Charge 子项只能判 PARTIAL。

### P1-5 Live 示例 Lock ID 错误

1. **描述**：`README.md` 和 `.env.example` 写 `bay-1`，现场账号实际使用 `lock-0001`。
2. **复现**：真实只读查询 `bay-1` 返回 `ECURIOUS_ACCESS_DENIED`；`lock-0001` 返回成功。
3. **根因**：模板保留开发占位 ID。
4. **位置**：`README.md:94-100`、`app_backend/.env.example:5`。
5. **建议**：使用明确占位符 `REPLACE_WITH_ASSIGNED_LOCK_ID`，现场部署文档记录 `lock-0001`，启动时校验 configured ID 属于 `GET /locks`。
6. **比赛影响**：新环境按 README 启动会显示无设备或权限错误。

## P2

### P2-1 No-Show 事件记录为 EXPIRED_WITHOUT_LOCAL_MATCH

No-Show 事件使用模拟未来时间，候选预约筛选要求事件时间 `< end_time`，因此事件无法绑定本地预约；随后 `refresh_active()` 又能把预约改为 `EXPIRED`。最终状态正确，但 Admin Event 缺失 reservation 关联。位置：`event_sync.py:126-147`、`388-393`。建议 expiry 事件优先按 provider reservation ID 映射，或对 expiry 使用不同时间匹配规则。

### P2-2 Admin UI 在 Live 模式仍显示 Mock provider

`frontend/app/src/pages/AdminPage.jsx:101` 硬编码 `Mock provider connected`，Demo Panel 也始终显示。Live 后端会安全拒绝 Demo 调用，但 UI 会误导运营人员。建议从 `/api/health` 获取 mode，Live 隐藏 Demo Panel并显示真实 provider/offline 状态。

### P2-3 Live 数据库保留 Mock Bay 映射

`database.py:288-304` 无条件插入 `BAY-001/002/003`，Live 再插入
`lock-0001`。实际 GE-26 返回 `BAY-001, lock-0001`，前者在真实 provider 无权限。前端会 catch 失败，但模型有脏映射。建议按 mode seed，或给 mapping 增加 provider/mode。

### P2-4 Blocked Bay 没有替代车位建议

检测、阻止预约、告警和恢复均通过，但用户没有“无替代车位”明确提示或替代建议。单真实 Bay 情况下应清楚显示无法自动改派。

### P2-5 文档没有 fixture 生成时间和许可说明

fixture 可验证为真实 GoingElectric 数据，但文件没有导出时间、请求范围、许可和刷新策略。建议添加 provenance 元数据或单独 DATA_SOURCE.md。

## P3

1. `frontend/app/test_goingElectric.py` 命名像 pytest，但本质是需要 Key 的手工脚本；建议改名 `scripts/check_goingelectric.py` 并文档化。
2. 前端 Footer 始终写 `Fictional Ecurious integration`；Live 模式应显示准确模式。
3. 建议为 acceptance 专项增加固定测试：Release failure、start+grace、SSE ordering、Mock presence-gated check-in。

---

# Part E — Hackathon Readiness

1. **五项 Mandatory Requirements 已完成几项？** 3/5 PASS；Requirement 2、3 为 PARTIAL。
2. **哪些功能完全可运行？** GoingElectric fixture 地图、认证、车辆、Mock 预约/冲突/取消、Mock No-Show、Mock Blocked Bay、Admin API、Mock Charging 状态、浏览器地图和站点详情。
3. **哪些模块只有 Mock 成功？** 完整 Reservation E2E、No-Show 全流程、Blocked Bay 全流程、Charging Start/Stop/Release。
4. **哪些模块接入真实 API？** Live REST locks/status/latest plate、SSE transport、Unlock/Lock/Whitelist/Keep-State Adapter、Live 本地预约与白名单协调、Manual Unlock API。
5. **完整 Reservation Lifecycle 是否成立？** Mock 成立；真实硬件不成立为已验证结论。
6. **No-Show 是否可以演示？** 可以，Mock PASS；Live timer 需修复。
7. **Blocked Bay 是否可以演示？** 可以，Mock PASS；无替代车位推荐。
8. **GoingElectric 数据是否真实？** 是静态真实目录数据，抽查与公开页面一致；不是实时状态。
9. **README 是否足够用于提交？** 基础启动和架构足够，但需修正 Lock ID、链接本报告、补充现场结果/限制/扩展性。
10. **当前能否进行完整比赛 Demo？** Mock 全流程可以；真实硬件完整预约 Demo 不建议宣称完成。
11. **最重要三个待修复问题？** P0 Live Release 一致性；P1 Live No-Show grace；P1 SSE 乱序保护。修复后再做真实白名单 + LPR + SSE + Release 单次现场闭环。

## 推荐 Demo 说法

可以明确展示：

1. GoingElectric 真实目录 fixture + 地图。
2. Mock 完整业务闭环与 No-Show / Blocked Bay 两个 Mission Areas。
3. 真实 API 只读状态、SSE 连接和团队人工验证的实体升降。
4. 清楚声明 Charging 为 Simulation，真实 LPR/白名单/完整生命周期仍需 Manual Check。

不要宣称：

- 欧洲车牌 LPR 已通过。
- 真实 EEPROM 白名单已通过本次验收。
- 真实 SSE 事件已经驱动预约签到。
- 实体升降成功等于完整 Reservation Lifecycle 成功。

## 下一步优先级

1. 修复 Live Release 为可恢复的两阶段状态，补失败测试。
2. 修复 Live No-Show `start + grace` 和 SSE timestamp/version ordering。
3. 现场执行一条受控的真实预约：白名单 -> LPR/Manual fallback -> occupied -> Charging Simulation -> vacant -> whitelist removal，并保存脱敏证据。
