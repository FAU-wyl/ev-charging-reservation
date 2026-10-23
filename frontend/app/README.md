# Ecurious React App

React + Vite + React Router + Leaflet 司机端与管理员端。保留原地图交互，站点、认证、车辆、预约和管理数据全部来自 App Backend，不直接调用 Ecurious Mock 或秘密 API Key。

```bash
npm ci
npm run dev
```

开发服务器为 <http://127.0.0.1:5173>，`/api` 默认代理到 <http://127.0.0.1:8001>。

验证：

```bash
npm run lint
npm test
npm run build
```

完整三服务启动、账号和 Demo 流程见仓库根目录 `README.md`。
