# 部署与复现

本说明对应新的独立案例入口。执行结果以 [验证记录](verification.md) 为准；首次构建不是经过离线验证的成品安装。

## 环境

- Python 3.11：运行配置工具；后端镜像同样采用 3.11。
- Node.js 22：前端镜像与 CI 的目标版本。
- Docker Linux Engine 和 Compose v2，可拉取 PostgreSQL/pgvector、Python、Node 与 Nginx 镜像。
- 演示端口 `127.0.0.1:18090` 空闲。

## 测试

从仓库根执行：

```bash
python deploy/case.py init test
python deploy/case.py check test
python deploy/case.py build test
python deploy/case.py test test
```

`init` 只在 `.case-state/test/` 生成本地配置。`check` 检查 Compose 实际渲染的项目、网络、端口及卷，不能证明应用运行成功。`build` 使用 hash lock；`test` 要求新建专属资源并运行 `pytest --require-pg`。已有同名资源会停止，工具不接管或清空它们。

真实结果写入 `.case-state/test/reports/backend-all.xml`。必需 PostgreSQL 用例的未收集、缺数据库和相关跳过不能作为成功。失败后的实例保留供调查，不自动反复运行或重置数据库。测试使用新容器内独占 `/tmp/jiuye-case-pytest`。

## 演示

```bash
python deploy/case.py init demo
python deploy/case.py check demo
python deploy/case.py build demo
python deploy/case.py start demo
python deploy/case.py seed demo
```

`seed` 仅接受本案例新建的 demo 数据库，通过标准种子函数生成模拟数据，并以同一份随机账号映射执行真实 HTTP 登录冒烟。它拒绝覆盖已有种子，不提供 reset 开关。

成功后，在本机查看 `.case-state/demo/accounts.json`，用其中的账号打开 `http://127.0.0.1:18090`。账号文件和配置文件需要按敏感文件保护；不要发到 issue、截图或提交到 Git。失败时账号映射保留，工具不会自动轮换密码后重跑。

## 状态与停止

```bash
python deploy/case.py status demo
python deploy/case.py stop demo
python deploy/case.py stop test
```

停止仅针对该案例项目，保留专属数据卷。本入口不提供一键删除或全局清理；需要重新测试时，应先理解失败原因与资源归属，再明确处理原实例。

## 配置约定

配置由工具独立生成，启动子进程仅继承必要系统环境并注入该案例配置，不自动读取旧 `.env`。外部 LLM 配置为空。应用运行网络为内部网络；当前 Compose 不配置真实模型服务，所以模型能力需另外验收。

数据库安装身份预检会连接数据库，因此不能单独证明目标隔离；工具先验证地址、拓扑与新资源，再执行应用端检查。不得替换成真实数据库地址，或把原项目目录、Docker socket、共享数据卷挂入案例。

Docker 基础镜像标签会随上游更新。实际复现需记录拉取的镜像摘要；本仓库只发布源码，不分发 Docker 镜像、模型或真实数据库。
