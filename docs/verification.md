# 验证记录

日期：2026-10-13。以下状态在发布前按实际执行结果更新，不以源码存在或命令退出前的局部输出代替验收。

## 当前执行结果

| 项目 | 环境 / 命令 | 结果 |
|---|---|---|
| 前端依赖安装 | Windows，Node 26.7.0 / npm 11.19.0；`npm ci --ignore-scripts` | 通过，安装 169 包；审计仍有 3 项 moderate 公告，详见 dependencies.md |
| 前端类型与生产构建 | `npm run build` → TypeScript + Vite 6.4.3 | 通过，3614 modules；17.36 秒为本机单次观测，不是性能基准 |
| 前端包体提示 | 主 JS 2150.82 kB，gzip 699.46 kB | 存在大于 500 kB 的构建警告；首版未作可选拆包优化 |
| 后端依赖 | Python 3.11.9；完整 hash lock 下载、离线安装、`pip check` | 通过，无依赖冲突；不等于漏洞为零 |
| 案例配置生成 | `python deploy/case.py init test` / `init demo` | 通过，分别生成独立配置并设置当前用户 ACL，未显示秘密值 |
| 两套实际 Compose 渲染 | `python deploy/case.py check test` / `check demo` | 通过：本机 context、internal 网络、专属卷、端口/挂载/构建路径符合约束 |
| 后端纯单元 | Python 3.11.9、FastAPI 0.136.3、PyJWT 2.14.0、cryptography 48.0.1、pytest 8.3.4 | 41 项通过，0 失败/错误/跳过；含合法 HS256、畸形 JSON header 与深嵌套 header 的真实解码回归 |
| required-PG 负向检查 | 17 个独立 pytest 进程 | 16 种错误配置在收集前拒绝；合法配置但纯测试选集在收集后拒绝；均为预期 exit 4，未连接数据库 |
| 部署入口边界 | `python deploy/test_case.py` | 9 项通过：配置注入、目标覆盖、秘密复用、远程 context、已有资源等拒绝路径；不是 Docker 运行测试 |
| PostgreSQL、容器构建、完整业务测试 | 本机 Docker Desktop Linux engine | 未执行成功：引擎启动失败，缺少可用 API；未改用 SQLite 计作通过 |
| 演示 seed、四角色 HTTP、四页面浏览器 | 需要独立可运行容器与数据库 | 未执行 |
| 真实模型推理与评估 | 未安装模型 | 未执行 |

CI 的目标 Node 版本为 22。上表前端本机通过使用 Node 26，不能直接当成 Node 22 运行证据。Docker 基础镜像尚未完成本次拉取与摘要冻结。

## 结果解释

- 保留原业务测试断言；必需 PostgreSQL 集合中的 skip/xfail、空集合或缺少通过记录会使任务失败。
- 联系方式加密 roundtrip 是纯函数测试，不能证明数据库落盘加密。
- provider 测试使用构造实现，不能证明真实 Ollama、模型解析质量或嵌入质量。
- 历史记录的 22 项实际通过和 32 项期望差异判定分别保留，不能宣称本版本 54 项全部通过。
- 没有真实运行截图时，不用设计图或 mock 截图充当产品运行证据。

## 复现入口

前端：`npm ci --ignore-scripts`、`npm run build`（目录 `frontend`）。

数据库与业务测试：仓库根依次运行 `python deploy/case.py init test`、`check test`、`build test`、`test test`。使用新建专属资源；运行命令及限制见 [部署说明](deployment.md)。

公开提交后可从仓库 Actions 查看对应提交的独立 CI 结果。绿色构建只能说明该次 workflow 的具体检查，不自动证明完整系统验收。
