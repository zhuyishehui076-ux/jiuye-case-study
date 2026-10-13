# 验证记录

日期：2026-10-13。本版按现有工程进度冻结为源码案例。**当前完整 CI 尚未通过：真实数据库测试 114 项，106 通过、8 失败、0 错误、0 跳过。** 前端与源码检查已通过；失败用例和业务断言继续保留。

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
| GitHub 前端构建 | Ubuntu / Node 22；`npm ci --ignore-scripts` + `npm run build` | 两轮 CI 均通过 |
| GitHub 源码检查 | 秘密扫描及自测、部署边界、无 CDN、无视觉依赖检查 | 两轮 CI 均通过 |
| Linux 后端测试镜像 | GitHub Ubuntu / Docker；`init test`、`check test`、`build test` | 两轮 CI 均通过 |
| 首轮真实 PostgreSQL 测试 | GitHub Docker / pgvector:pg15；114 项，JUnit 记录 | 80 通过、5 失败、29 错误、0 跳过；随后修复上传卷初始化、测试种子清场和重复解析记录准备 |
| 修复后的真实 PostgreSQL 测试 | 同一工作流、全新独立数据库与卷；114 项 | **106 通过、8 失败、0 错误、0 跳过，任务仍为失败**；差异见 [测试状态](test-status.md) |
| 修复后的本地窄回归 | 目标配置、档位守卫与 JWT，Python 3.11 | 25 项通过；不作为新增 25 项独立业务能力统计 |
| 首提交匿名下载 | 无 GitHub 凭据的全新 clone | 809 文件逐 Git blob 与源提交一致；167 Python 文件语法通过、部署边界 9 项通过；不等于业务验收 |
| 本机 Docker Desktop | Linux engine 启动 | 失败：缺少可用 API；云端测试与本机状态分开记录 |
| 演示 seed、四角色 HTTP、四页面浏览器 | 需要独立可运行容器与数据库 | 未执行 |
| 真实模型推理与评估 | 未安装模型 | 未执行 |

首轮云端结果对应提交 `ecfbb723927d4e1605cbd31fd1649b637ca9d94a`，见 [首轮 Actions](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38416679440)。修复后结果对应代码提交 `bc0244a94079fcaa96fffd17faf367fbde599f00`，见 [第二轮 Actions](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38419852269)。版本冻结前只补充验证文档，不再修改该次受测代码，也不重复运行相同测试。

本机 Node 26 与云端 Node 22 分别实际执行。Docker 基础镜像使用标签，尚未冻结摘要；再次构建可能拉取不同镜像字节。

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
