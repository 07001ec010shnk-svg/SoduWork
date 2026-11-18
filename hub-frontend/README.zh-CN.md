# SoduWork 管理端

**简体中文** · [English](README.md) · [开发指南](../docs/development.md#管理页面)

命令示例中的 `sh start.sh` 适用于 macOS/Linux；Windows PowerShell 使用 `.\start.cmd`，命令提示符使用 `start.cmd`，参数相同。独立 Python 工具示例使用 `python3`；Windows 使用 `py`，已激活虚拟环境时使用 `python`。

React + Vite 应用，对接 `../hub-backend` 的管理 API。当前入口为 `src/main.jsx`，路由与页面在 `src/` 维护；设计原型不参与应用运行。

## 职责

管理组织与账号、客户端与执行网络、模型与能力、任务与审批、插件和发布配置。权限由 Hub 校验，前端隐藏按钮不能替代接口权限检查。

模型目录默认显示服务、模型数量与实时负载的紧凑摘要；展开概览可查看配置、近一小时统计和请求分布，收起时仍可操作服务和并发设置。服务按启用优先排序，同一启停状态内自定义服务优先。新建服务当次会移到前面便于定位，重新进入后恢复上述稳定分组顺序。

服务卡片根据内容区宽度自适应为一至四列；点击“查看”打开模型详情弹窗，服务网格的大小和位置保持不变。添加模型、发现模型和配置服务保持可见；启停与删除收进“更多”浮层，删除继续保留原有确认。

## 开发运行

先起后端（另一个终端）：
```bash
cd ../hub-backend
.venv/Scripts/python -m uvicorn app.main:app --port 9080
```
再起前端：
```bash
npm ci             # 按锁文件安装
npm run dev        # http://localhost:5173
```
Vite 将 `/api`、`/healthz` 和 `/readyz` 代理到 `127.0.0.1:9080`（见 `vite.config.js`）。如果配合根目录 `sh start.sh` 使用，先通过 `SODUWORK_DEV_HUB` 将代理指向 `http://127.0.0.1:18989`，具体命令见[开发指南](../docs/development.md#管理页面)。登录使用 Hub 的用户账号；首次初始化管理员由 Hub 的 `ADMIN_USER` / `ADMIN_PASS` 配置产生，后续通过用户管理维护，不是前端写死账号。

## 验证与构建
```bash
node --test tests/*.test.mjs
npm run build      # dist/ 为生成物，由 Hub 或部署环境托管
```

浏览器场景见 `tests/` 中的独立运行脚本；真实环境测试与隔离页面夹具应分别记录。`dist/`、依赖和本地配置不提交到 Git。

构建后运行 `node tests/run-model-catalog-layout-browser.cjs /absolute/evidence/directory`，检查模型目录的四语言布局、折叠、轮询及重新加载排序。需安装客户端 Electron 依赖，或通过 `SODUWORK_ADMIN_TEST_ELECTRON` 指定其可执行文件。场景使用本地合成响应，不连接真实 Hub 或模型服务。
