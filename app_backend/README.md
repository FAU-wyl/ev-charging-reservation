# App Backend

FastAPI 业务后端，支持 `ECURIOUS_API_MODE=mock` 与 `ECURIOUS_API_MODE=live`。Live 模式连接官方 ECURIOUS REST/SSE，预约与用户数据仍保存在本地 SQLite。

Mock 启动：

```bash
cd app_backend
source ../.venv/bin/activate
set -a
source .env.mock.example
set +a
python -m uvicorn app_backend.main:app --host 127.0.0.1 --port 8001 --reload
```

Live 启动：复制 `.env.example` 为被 Git 忽略的 `.env`，填写 `ECURIOUS_API_KEY` 后加载并运行相同的 Uvicorn 命令。

团队现场设备配置为 `ECURIOUS_LOCK_ID=lock-0001`；`BAY-001` 等 ID 仅属于
Mock。Live Release 使用 `RELEASE_PENDING/FAILED/UNKNOWN` 持久协调状态，硬件
确认撤权且物理占用为 `free` 后才完成。No-Show 使用预约开始时间加配置 grace。
SSE 按 Lock 和事件类型保存独立水位线，过时或无效时间戳不覆盖新状态。
Live 模式还会按 `ECURIOUS_PLATE_POLL_SECONDS`（默认 5 秒）只读查询最新车牌；
SSE 车牌为空时立即回查相同接口，只有取得非空且不匹配的车牌才产生告警。

模拟充电会话由 App SQLite 持久化，司机可调用：

```text
POST /api/charging/sessions
GET  /api/charging/sessions/{sessionId}
POST /api/charging/sessions/{sessionId}/stop
POST /api/reservations/{reservationId}/depart
```

时长和费用由服务端按 `SIMULATED_CHARGING_RATE_EUR_PER_HOUR` 计算，并始终
标记为 Simulation / Estimated。

司机停止充电后可确认车辆已离开。Live 模式会先确认车位为 `free`、结束预约并
撤销车牌授权，再次确认安全状态后发送升锁命令；`occupied`、`unknown` 或离线
状态不会发送升锁命令，重复确认也不会重复发送。

只读设备验证：

```bash
python scripts/verify_live_readonly.py
```

人工确认的实体硬件 demo（必须先启动本机 `app_backend`）：

```bash
# 默认只读，不发送硬件命令
python scripts/demo_hardware_test.py --lock-id lock-0001

# 降锁后再升锁；两次动作前都会要求输入精确确认文本
python scripts/demo_hardware_test.py \
  --action cycle --allow-write --lock-id lock-0001
```

脚本通过本机 FastAPI 调用硬件，要求本地 ADMIN 登录，并在写入前检查
Live 模式、设备在线、车位空闲、无进水和无活动错误。仅传
`--allow-write` 不会自动操作；每条 `unlock` / `lock` 指令仍须操作员在终端
输入 `UNLOCK <lock-id>` / `LOCK <lock-id>`。确认不匹配、非交互终端、状态
未物理确认或轮询超时都会停止，且不会继续发送下一条命令。该 demo 不修改
白名单或 `keep-state`。

测试：

```bash
../.venv/bin/python -m pytest
```

完整架构、安全规则、Windows 命令和实体硬件待验证项见仓库根目录 `README.md`。
