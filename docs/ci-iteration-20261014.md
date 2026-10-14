# 下一轮 CI 修复记录 · 2026-10-14

开发分支：`fix/ci-fixture-contracts-20261014`。起点为 `f9e4393b4c121e0df721817b6b41a55d2cb8f25c`；它相较原受测提交 `bc0244a94079fcaa96fffd17faf367fbde599f00` 仅有文档变化。`main`、Release 及已发布的 `v0.1.0-case-study` 均不在本轮写入范围内。

本轮只修改三个测试文件及这份记录。FastAPI 路由、权限依赖、业务服务、数据模型、迁移、依赖锁及 CI 工作流保持原实现；没有删除原用例、移除业务断言或增加 skip/xfail。旧版的 [测试状态](test-status.md) 和 [验证记录](verification.md) 继续作为冻结版本证据。

## 实际修复

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

## 真实验证

第一轮开发分支受测提交：`593d7fca24710a7c9fe16d729cac80a92f251290`，见 [Actions 38559685770](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38559685770)。前端与源码检查成功；真实 PG **115 项，110 通过、5 失败**，required-PG 选集 62 项、57 通过、0 skip/xfail。该轮验证揭示首次选中的帮扶档案已有两条故事种子记录，已据此修正选取条件。

第二轮受测提交：`651095ab63815c74b349dd9d0abac51cef46a35d`，见 [Actions 38560287537](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38560287537)。已实际读取三项任务状态及 PostgreSQL 最终日志：前端和源码检查成功；真实 PG **115 项，111 通过、4 失败、0 错误、0 跳过**，required-PG 选集 62 项、58 通过、0 skip/xfail。耗时 104.02 秒为该轮单次观测。**完整 CI 仍失败。** 新增 1 项零分回归，因此总数从冻结版的 114 变为 115，不能把新增测试统计成某个原失败用例已经通过。

五项原参数失配均已补齐。其中两项帮扶、岗位审核、画像重建这四项原失败用例已通过；岗位录入复合用例继续在 demo 禁止上传处失败。另两项看板用例仍失败；推送复合用例已验证 10 人推送及重放/审计后，在 CSV 表头断言失败。权重隔离与新增零分拒绝回归通过，原推送 409 前置条件已用真实 PG 单独复现。

后续文档提交使用 `[skip ci]`，仅保存已核验结果；上述测试文件与业务实现不再变更。它没有新的业务测试通过结论，CI 证据仍严格绑定第二轮受测提交；没有把失败用例转成跳过。

本地仅完成 Python 语法、diff 格式、秘密扫描及自测、部署入口 9 项边界检查、无 CDN 与无视觉依赖检查。本地没有 Docker/PostgreSQL 或完整后端依赖；数据库业务结果以上述 GitHub 独立容器的实际日志为准。

## 剩余冲突与下一版本

1. **看板读取规则冲突**：`test_all_roles_read_dashboard` 要求管理员和领导读取 summary / colleges / trend / alerts 均为 200。当前实现按端点分工；管理员在 colleges/trend 无权限，领导在 alerts 无权限。路由文件顶部“四角色皆可见、无写入口”的旧说明也与代码不符。缺少原工程需求文档，不能凭旧注释扩大数据读取权限。需确认最终读取矩阵，再同步业务断言与说明。
2. **看板写接口规则冲突**：`test_dashboard_has_no_write_routes` 要求看板所有端点仅 GET/HEAD；当前 `PUT /dashboard/target` 仅允许就业办，强制规范幂等键、`expected_revision` 比较、行锁和审计。需确认保留该目标设置接口，还是把设置移至另一个命名空间；不通过删除功能或放开角色来消除失败。
3. **XLSX 测试档位冲突**：CI 明确运行 demo + local；`Settings.allow_user_file_uploads` 仅 production 为真，`require_user_file_uploads` 在 demo 返回 403。这是现有保护的预期拒绝，合法幂等键不能使其成为成功路径。原“HTTP 成功导入两行”断言仍失败；下一步需建立与演示身份和数据分离的 production 档测试环境，分别验收 demo 拒绝上传与生产成功导入，不覆盖依赖、关闭保护或将 demo 临时冒充生产。
4. **CSV 表头冲突**：原推送复合用例在修复前停于推送 409；当前能完成推送及重放后，抵达原 CSV 断言。测试要求 `student_no,student_name,college_id,major_id,score,reason_text`；服务实际输出带 BOM 的 `学号,姓名,学院编号,专业编号,匹配度,推荐理由`。需确认用于机器对接的英文协议，还是面向用户的中文导出；本轮不改任一约定。

读取权限的当前代码矩阵（静态核对，不宣称各格均做过 HTTP 验收）：

| 端点 | 管理员 | 就业办 | 辅导员 | 只读领导 |
|---|---|---|---|---|
| GET summary | 允许 | 允许 | 拒绝 | 允许 |
| GET colleges / trend / college oversight | 拒绝 | 允许 | 拒绝 | 允许 |
| GET alerts | 拒绝 | 允许 | 仅本学院 | 拒绝 |
| GET my-college-summary | 拒绝 | 拒绝 | 仅本学院 | 拒绝 |
| GET target | 允许 | 允许 | 允许 | 允许 |
| PUT target / GET target-operations | 拒绝 | 允许 | 拒绝 | 拒绝 |

另有一项真实前端问题：`frontend/src/pages/Dashboard.tsx` 对四个统计端点使用无角色分流的 `Promise.all`。按当前矩阵，管理员、领导或辅导员会遇到至少一个 403，整个组合不会进入成功赋值分支。这是从源码确定的请求组合缺陷；本轮未运行浏览器，不把该结论标为浏览器验收。确认读取矩阵后应修正页面请求和导航边界。

用户所需最少决策为：看板读取矩阵、目标设置接口归属、CSV 表头协议。demo 禁止上传已有明确实现和保护说明；建立 production 测试环境属于后续工程工作，无须为本轮临时放宽该保护。

## 复现命令与未验证项

在上述开发分支的全新 checkout、配置和专属资源上，依次从仓库根运行：

```bash
python deploy/case.py init test
python deploy/case.py check test
python deploy/case.py build test
python deploy/case.py test test
```

JUnit 输出为 `.case-state/test/reports/backend-all.xml`。最后一条在剩余约定冲突解决前仍应失败，不绕过 `--require-pg`、不删除用例或改成 xfail。

```bash
python scripts/ci/check_secrets.py --self-test --tracked
python deploy/test_case.py
bash scripts/ci/check_no_cdn.sh
bash scripts/ci/check_no_vision_deps.sh
```

前端验证在 `frontend` 中执行 `npm ci --ignore-scripts`、`npm run build`。

未验证：独立 production 档 XLSX HTTP 成功路径及两行落库、最终权限/目标/CSV 规则、真实浏览器四角色流程、真实模型效果、并发竞态和生产部署。本轮幂等及版本回归覆盖顺序请求和过期版本拒绝，不宣称并发验收。

发布保护：冻结标签对象为 `95602e31b4c1bd432ea883060e3ad9555daff27b`，指向 `f9e4393b4c121e0df721817b6b41a55d2cb8f25c`。本轮未发布新版本、合并到 main、修改标签或替换发布附件。
