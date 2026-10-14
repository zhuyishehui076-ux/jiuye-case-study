# 下一轮 CI 修复记录 · 2026-10-14

开发分支：`fix/ci-fixture-contracts-20261014`。起点为 `f9e4393b4c121e0df721817b6b41a55d2cb8f25c`；它相较原受测提交 `bc0244a94079fcaa96fffd17faf367fbde599f00` 仅有文档变化。`main`、Release 及已发布的 `v0.1.0-case-study` 均不在本轮写入范围内。

**最新结果：提交 `2dd52083caff6cb095038574074c62389fd4492b` 的完整 CI 已通过。** [push Actions 38580304827](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38580304827) 的前端、源码检查、真实 PostgreSQL 三项任务均成功；已实际下载并解析 [JUnit 附件](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38580304827/artifacts/11901839776)：**128 通过、0 失败、0 错误、0 跳过**。下面保留迭代过程中的失败证据，不将旧版或中间提交改写为通过。

第一阶段只修改三个测试文件及这份记录。该阶段 FastAPI 路由、权限依赖、业务服务、数据模型、迁移、依赖锁及 CI 工作流保持原实现；没有删除原用例、移除业务断言或增加 skip/xfail。旧版的 [测试状态](test-status.md) 和 [验证记录](verification.md) 继续作为冻结版本证据。用户随后确认三项规则，第二阶段按该确认更新业务断言并提供双语表头，见下文。

## 第一阶段修复

| 文件 / 用例 | 最小修复及保留的保护 |
|---|---|
| `test_assist.py` · 追加记录 | 每个新操作使用独立、规范 UUIDv4 幂等键；相同请求重放使用原键。验证返回原记录、不同内容复用键返回 409、缺键返回 422、两次追加只有两条记录和两条追加审计。选择没有既有记录的模拟档案，保留“总数为 2”的原断言，不删除种子历史。 |
| `test_assist.py` · 学院范围 | 越院写入携带合法幂等键，实际到达范围检查并保留 403 断言。 |
| `test_assist.py` · 导出准备 | 补齐准备请求的幂等键并检查 200，避免记录未写入却继续验收导出。 |
| `test_job_matching.py` · 审核 | 从已落库解析记录读取 `review_revision` 并传入 `expected_revision`。验证版本加一、审计记录前后版本，以及过期版本不能覆盖已审核内容。 |
| `test_job_matching.py` · 岗位录入 | 粘贴、XLSX 和领导写入拒绝请求分别携带独立合法幂等键；先验证粘贴、邮件预留 501 和领导写入 403。保留 XLSX 成功导入两行的原断言，demo 档的上传保护不绕过。 |
| `test_profiles.py` · 重建 | 补齐重建幂等键；保留 2100 条重建结果、落库数量、画像证据与辅导员学院范围断言。 |
| `test_job_matching.py` · 权重隔离 | 权重覆盖只在事务内 flush 后读取，关闭会话回滚；随后用新会话确认原设置不变，避免仅专业权重污染后续用例。 |
| `test_job_matching.py` · 推送准备 | 使用种子中真实存在的专业代码，明确验证 10 条未推送、正分匹配。保留成功推送 10 人；验证重放推送为 0、已推送为 10、仅一条推送审计。CSV 原表头断言继续保留。 |
| 新增零分回归 | 在共享 `db` 夹具的可回滚 PG 事务内复现“仅专业权重 + 无专业命中”的旧前置条件；验证得到 10 条零分匹配，拒绝推送，不写推送时间和推送审计。 |

## 第一阶段真实验证

第一轮开发分支受测提交：`593d7fca24710a7c9fe16d729cac80a92f251290`，见 [Actions 38559685770](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38559685770)。前端与源码检查成功；真实 PG **115 项，110 通过、5 失败**，required-PG 选集 62 项、57 通过、0 skip/xfail。该轮验证揭示首次选中的帮扶档案已有两条故事种子记录，已据此修正选取条件。

第二轮受测提交：`651095ab63815c74b349dd9d0abac51cef46a35d`，见 [Actions 38560287537](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38560287537)。已实际读取三项任务状态及 PostgreSQL 最终日志：前端和源码检查成功；真实 PG **115 项，111 通过、4 失败、0 错误、0 跳过**，required-PG 选集 62 项、58 通过、0 skip/xfail。耗时 104.02 秒为该轮单次观测。**完整 CI 仍失败。** 新增 1 项零分回归，因此总数从冻结版的 114 变为 115，不能把新增测试统计成某个原失败用例已经通过。

五项原参数失配均已补齐。其中两项帮扶、岗位审核、画像重建这四项原失败用例已通过；岗位录入复合用例继续在 demo 禁止上传处失败。另两项看板用例仍失败；推送复合用例已验证 10 人推送及重放/审计后，在 CSV 表头断言失败。权重隔离与新增零分拒绝回归通过，原推送 409 前置条件已用真实 PG 单独复现。

第一阶段文档提交 `d1e794e85301add8b8ac13d966d45c9ed6cbd950` 使用 `[skip ci]`，仅保存已核验结果；相较该阶段第二轮受测提交，没有测试文件或业务实现变化。它没有新的业务测试通过结论，阶段证据仍严格绑定对应受测提交；没有把失败用例转成跳过。

本地仅完成 Python 语法、diff 格式、秘密扫描及自测、部署入口 9 项边界检查、无 CDN 与无视觉依赖检查。本地没有 Docker/PostgreSQL 或完整后端依赖；数据库业务结果以上述 GitHub 独立容器的实际日志为准。

## 第二阶段：用户已确认的规则与实现

用户明确选择：保留现有看板分工；保留就业办专用、受保护的 `PUT /dashboard/target`；分别提供中文和英文两种 CSV 表头。

本阶段受测代码提交：`3dfb5a96990edffbdbd9ddbb07b2c9b7b1972e65`，见 [Actions 38577018476](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38577018476)。沿用原开发分支及草稿 PR，不合并到 main、不发布新版本。修改范围为：看板路由的旧说明、CSV 导出路由与服务、看板和导出业务测试、对应前端请求/导航和导出控件。权限依赖、目标写入实现、模型、迁移、依赖锁和 CI 工作流未修改。

- 原“管理员和领导四类端点一律 200”断言按用户确认改为四角色允许/拒绝矩阵；覆盖 7 类读取端点，以及辅导员明细和统计仅属本学院。
- 原“看板无任何写方法”断言按用户确认改为只允许 `PUT /dashboard/target` 这一写入口；新增实际请求回归，验证管理员/辅导员/领导写入及操作回执读取被拒绝、缺失或非法幂等键被拒绝、缺失版本被拒绝、就业办更新与重放、冲突键、过期版本拒绝，以及只新增一条目标更新审计。真实 PG 会话使用既有可回滚测试事务，认证与权限/写入守卫均执行原实现。
- CSV 的默认中文协议保持，新增 `header_language=zh|en` 查询参数；两种语言仅改变表头与下载文件名，数据值、字段顺序、UTF-8 BOM、正分过滤和学院范围保持一致。非法语言返回 422；导出审计新增 `header_language`。原英文表头断言保留在英文请求上，并验证中文默认、两种内容一致、辅导员学院范围、管理员/领导拒绝和成功导出次数。
- 看板页面按角色请求：管理员只请求汇总；领导请求汇总/学院列表/趋势；就业办请求汇总/学院列表/趋势/明细；辅导员请求本院统计/本院明细。等待真实身份加载后展示导航，管理员和领导不进入学生明细页面；增加数据加载错误提示及过期请求清理。
- 岗位页面分别提供中文和英文 CSV 导出按钮；客户端显式传递表头语言。

接口示例（均须原有合法身份）：

```text
GET /api/v1/jobs/{job_id}/export                       # 默认中文表头
GET /api/v1/jobs/{job_id}/export?header_language=zh    # 中文表头
GET /api/v1/jobs/{job_id}/export?header_language=en    # 英文表头
```

中文表头：`学号,姓名,学院编号,专业编号,匹配度,推荐理由`。
英文表头：`student_no,student_name,college_id,major_id,score,reason_text`。

本地 `npm ci --ignore-scripts`、`npm run build` 已通过（TypeScript + Vite；169 包、3614 modules）。源码秘密扫描及自测、部署边界 9 项、无 CDN、无视觉依赖和 Python 语法检查通过。该阶段真实 PG 日志为 **119 项：118 通过、1 失败**；required-PG 66 项、65 通过。看板读取矩阵、目标写入保护和双语 CSV 均通过，唯一失败仍是 demo 档 XLSX 成功导入断言。该结果是中间状态，不作为最终 CI 通过证据。

该阶段保留了 XLSX 原成功断言及失败，没有覆盖上传依赖、切换 demo 身份、放宽上传开关或转成跳过。随后在第三阶段建立独立测试环境，保留“成功导入两行”的验收目标并在正确的档位验证。

## 第三阶段：独立 production 档 XLSX 验证与最终结果

受测提交：`2dd52083caff6cb095038574074c62389fd4492b`。本阶段仅补充测试夹具、独立子进程验证程序和配置拒绝用例；生产业务代码、权限/上传守卫、共享 demo 安装身份、迁移、Compose、部署入口和工作流均未更改。

`production_import_probe` 依赖既有 `pg_engine`，先核对共享测试库的实际数据库身份、档位、安装标识和凭据。仅在案例 Compose 的私有 `postgres:5432` / `case_user` 测试容器内，创建随机 nonce 命名的全新数据库和账号；如果名称已存在则拒绝继续。新安装标识、JWT/字段密钥、数据库账号密码、用户密码及上传目录均独立生成，不复用 demo 库或命名空间。

初始化子进程以明确 `APP_PROFILE=production` 配置进行迁移前只读预检、迁移及安装身份/目录标记创建。只准备两名合成管理账号，不调用 demo seed。正式 API 验证在另一个 production 子进程里，以 `NOSUPERUSER / NOCREATEDB / NOCREATEROLE / NOINHERIT` 数据库账号执行；仅授权新测试库的连接、public schema 使用、表数据操作与序列使用。没有修改全局 settings、伪造登录令牌或覆盖依赖。

同一个原岗位录入用例现在明确验证两条路径：

| 路径 | 实际验收 |
|---|---|
| 共享 demo 应用 | 合法就业办身份及幂等键上传仍返回 403；父进程 settings 和原库安装身份保持 demo |
| 独立 production 应用 | 正式登录及会话校验、匿名请求 401、领导上传 403、缺幂等键 422、成功导入 200 且返回两行、落库两行、相同键/内容重放仍 200 且 ID 不变、只有一条导入审计和一条操作回执 |
| 资源收尾 | 子进程释放连接；仅删除本次成功创建且数据库所有者仍符合预期的库和账号，不强制断开连接，不接管或删除原有资源；fixture teardown 无错误 |

新增配置拒绝测试覆盖错误数据库路径、外部主机、错误数据库账号、demo 档位、错误安装标识、复用 demo 上传目录、共享密钥和缺少数据库密码；另有一个合法配置接受用例。这些是 9 项纯配置检查，不虚报为 9 项生产业务验收，也不连接外部数据库。

最终 [push 工作流](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38580304827) 三项任务成功。已读取 PostgreSQL 任务日志并下载其 JUnit，ZIP SHA-256 与 GitHub 元数据一致：`c6beb55281449bfaf4fbed470c1c394bf3c149b06f544f91af962f7d28d6fab8`。JUnit 声明和逐 testcase 计数均为 **128 项，0 失败/错误/跳过**；单次测试耗时 115.343 秒。原 8 项失败的验收目标均有对应修复/确认后的实际结果，没有删除用例或增加 skip/xfail。总数从第二阶段 119 增至 128，增量来自上述 9 项配置检查。

同一受测提交自动触发的 [PR 工作流](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38580314678) 三项任务也成功；它与 push 运行属于重复验证，不能合计为 256 项能力。最终文档提交只记录证据，业务及测试文件与上述受测提交相同，不另宣称文档提交进行了新一轮数据库测试。

## 第一阶段遗留差异（确认规则前的历史记录）

1. **看板读取规则冲突**：`test_all_roles_read_dashboard` 要求管理员和领导读取 summary / colleges / trend / alerts 均为 200。当前实现按端点分工；管理员在 colleges/trend 无权限，领导在 alerts 无权限。路由文件顶部“四角色皆可见、无写入口”的旧说明也与代码不符。缺少原工程需求文档，不能凭旧注释扩大数据读取权限。需确认最终读取矩阵，再同步业务断言与说明。
2. **看板写接口规则冲突**：`test_dashboard_has_no_write_routes` 要求看板所有端点仅 GET/HEAD；当前 `PUT /dashboard/target` 仅允许就业办，强制规范幂等键、`expected_revision` 比较、行锁和审计。需确认保留该目标设置接口，还是把设置移至另一个命名空间；不通过删除功能或放开角色来消除失败。
3. **XLSX 测试档位冲突**：CI 明确运行 demo + local；`Settings.allow_user_file_uploads` 仅 production 为真，`require_user_file_uploads` 在 demo 返回 403。这是现有保护的预期拒绝，合法幂等键不能使其成为成功路径。原“HTTP 成功导入两行”断言仍失败；下一步需建立与演示身份和数据分离的 production 档测试环境，分别验收 demo 拒绝上传与生产成功导入，不覆盖依赖、关闭保护或将 demo 临时冒充生产。
4. **CSV 表头冲突**：原推送复合用例在修复前停于推送 409；当前能完成推送及重放后，抵达原 CSV 断言。测试要求 `student_no,student_name,college_id,major_id,score,reason_text`；服务实际输出带 BOM 的 `学号,姓名,学院编号,专业编号,匹配度,推荐理由`。需确认用于机器对接的英文协议，还是面向用户的中文导出；本轮不改任一约定。

读取权限的确认后矩阵（7 类读取端点已在第二、三阶段按四角色执行允许/拒绝 HTTP 检查；操作回执及目标修改权限在保护回归中验证）：

| 端点 | 管理员 | 就业办 | 辅导员 | 只读领导 |
|---|---|---|---|---|
| GET summary | 允许 | 允许 | 拒绝 | 允许 |
| GET colleges / trend / college oversight | 拒绝 | 允许 | 拒绝 | 允许 |
| GET alerts | 拒绝 | 允许 | 仅本学院 | 拒绝 |
| GET my-college-summary | 拒绝 | 拒绝 | 仅本学院 | 拒绝 |
| GET target | 允许 | 允许 | 允许 | 允许 |
| PUT target / GET target-operations | 拒绝 | 允许 | 拒绝 | 拒绝 |

第一阶段发现的前端问题是 `frontend/src/pages/Dashboard.tsx` 对四个统计端点使用无角色分流的 `Promise.all`。第二阶段已按确认后的权限修正请求和导航，前端类型及构建通过；未运行真实浏览器，因此不将源码修复和构建计作浏览器验收。

三个业务决策已由用户确认并全部落实。production 档测试缺口已用独立临时环境补齐，demo 禁止上传的保护维持原约定。当前无剩余 CI 失败或阻塞业务决策。

## 复现命令与未验证项

在上述开发分支的全新 checkout、配置和专属资源上，依次从仓库根运行：

```bash
python deploy/case.py init test
python deploy/case.py check test
python deploy/case.py build test
python deploy/case.py test test
```

JUnit 输出为 `.case-state/test/reports/backend-all.xml`。最后一条保持 `--require-pg`，实际执行共享 demo 与独立 production 库的验收；最新受测提交应退出 0。新增 production 夹具只支持上述案例 Compose 私有临时 PostgreSQL 服务，其他服务器会失败关闭，不能拿已有生产数据库替代。

```bash
python scripts/ci/check_secrets.py --self-test --tracked
python deploy/test_case.py
bash scripts/ci/check_no_cdn.sh
bash scripts/ci/check_no_vision_deps.sh
```

前端验证在 `frontend` 中执行 `npm ci --ignore-scripts`、`npm run build`。

尚未验证：真实浏览器四角色流程和下载交互、真实模型效果、并发竞态、生产部署及迁移运维。production 档 XLSX 在合成数据、独立临时真实 PG、ASGI TestClient 的完整 API 层验证通过，不等于已部署真实生产服务器。本轮幂等及版本回归覆盖顺序请求和过期版本拒绝，不宣称并发验收。

发布保护：冻结标签对象为 `95602e31b4c1bd432ea883060e3ad9555daff27b`，指向 `f9e4393b4c121e0df721817b6b41a55d2cb8f25c`。本轮未发布新版本、合并到 main、修改标签或替换发布附件。
