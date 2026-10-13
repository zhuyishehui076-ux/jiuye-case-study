# 当前测试状态

代码版本：`bc0244a94079fcaa96fffd17faf367fbde599f00`。见 [云端 CI 与 JUnit 附件](https://github.com/zhuyishehui076-ux/jiuye-case-study/actions/runs/38419852269)。

前端构建与源码检查通过。后端完整选集 **114 项：106 通过、8 失败、0 错误、0 跳过**。本版保留全部原业务断言，按当前进度冻结为源码案例预发布。

## 8 项实际失败

| 用例 / 位置 | 实际结果与现有约定 | 冻结状态 |
|---|---|---|
| `test_assist_record_append_only_and_no_delete_route` · [test_assist.py](../backend/tests/test_assist.py) | 追加请求缺少规范 UUIDv4 `Idempotency-Key`，返回 422；旧断言要求 200 | 请求夹具待同步；追加和审计断言保留 |
| `test_counselor_assist_cases_limited_to_own_college` · [test_assist.py](../backend/tests/test_assist.py) | 同样缺少幂等键，先返回 422，尚未到达旧断言要求的 403 权限检查 | 请求夹具待同步；不能将此结果当作学院隔离验证通过 |
| `test_all_roles_read_dashboard` · [test_dashboard_pg.py](../backend/tests/test_dashboard_pg.py) | 学院列表当前允许就业办、校领导读取；管理员返回 403，旧测试要求 200 | 权限约定冲突，需先确认业务规则 |
| `test_dashboard_has_no_write_routes` · [test_dashboard_pg.py](../backend/tests/test_dashboard_pg.py) | 当前存在就业办专用 `PUT /dashboard/target`，带幂等、版本与审计保护；旧测试要求整个看板无写入口 | 接口约定冲突；保留现有功能和旧断言 |
| `test_review_flow_updates_parse_and_writes_audit_log` · [test_job_matching.py](../backend/tests/test_job_matching.py) | 旧调用未提供服务必需的 `expected_revision` 参数 | 调用夹具待同步；审核更新与审计断言保留 |
| `test_job_entry_endpoints_cover_paste_xlsx_and_email_stub` · [test_job_matching.py](../backend/tests/test_job_matching.py) | 粘贴请求缺少必需的 `Idempotency-Key`，返回 422；旧断言要求 200 | 本次后续 XLSX / 邮件分支未完成验收 |
| `test_push_confirm_and_export_match_list` · [test_job_matching.py](../backend/tests/test_job_matching.py) | 推送返回 409；现代码拒绝没有正分或已推送匹配的推送请求，旧测试要求成功 | 前置匹配状态待核对，资格守卫保持 |
| `test_profile_rebuild_endpoint_and_counselor_scope` · [test_profiles.py](../backend/tests/test_profiles.py) | 重建请求缺少必需的 `Idempotency-Key`，返回 422；旧断言要求 200 | 请求夹具待同步；重建数量与学院范围断言保留 |

岗位推送失败的一个待验证解释是：前序模块用例持久化了“仅专业”匹配权重，而后续推送夹具没有专业代码，可能得到零分。此处是依据代码顺序的推断，尚未执行独立隔离实验，不能当成已修复或已证明的根因。

五项请求或调用夹具可考虑补齐合法参数；两项看板差异需要明确业务约定；推送用例需要核对状态隔离。首版没有继续调整这些行为，也没有删除失败用例、转为跳过或放宽保护检查。

## 如何理解本版

源码、构建与部分测试结果可用于阅读和继续开发；这 8 项失败仍会让当前完整 CI 返回失败。真实模型、完整浏览器流程和生产部署均未因此得到验证。其他实际结果见 [验证记录](verification.md)。
