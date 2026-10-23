# App Backend

这是 React Driver/Admin App 与 fictional Ecurious Mock 之间的独立业务后端。完整架构、启动和 Demo 步骤请看仓库根目录 `README.md`。

快速启动：

```bash
cd app_backend
source ../.venv/bin/activate
set -a; source .env.example; set +a
python -m uvicorn app_backend.main:app --host 127.0.0.1 --port 8001 --reload
```

Swagger：<http://127.0.0.1:8001/docs>

测试：

```bash
../.venv/bin/python -m pytest
```

