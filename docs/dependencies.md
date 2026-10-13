# 依赖基线、哈希锁与安全公告

核对日期：2026-10-13。本案例保留业务范围，只修复依赖漂移及有依据的安全问题。锁定版本并不等于全部安全或全平台已测试；本页明确记录保留版本、适用边界与待补证据。

## 安装入口

- `backend/requirements.txt` 是精确直接运行依赖；`requirements.lock` 是完整哈希安装入口。
- `backend/requirements-dev.txt` 引用运行清单并约束到运行锁；`requirements-dev.lock` 包含运行与 pytest/ruff 的完整哈希依赖。运行环境使用 runtime 锁，不安装 pytest。
- 两锁由已有 uv 0.12.5 按 Python 3.11、universal、generate-hashes、no-build 解析，逐项存在 SHA256，运行/开发同名包版本一致。平台标记（例如非 Windows 的 uvloop）保留；universal 解析不是所有平台构建通过的证明。
- `frontend/package.json` 与 `package-lock.json` 的直接版本一致；依赖更新使用 `--package-lock-only --ignore-scripts`，没有执行 npm lifecycle。

```bash
python -m pip install --require-hashes -r backend/requirements.lock
# 独立测试环境改用：
python -m pip install --require-hashes -r backend/requirements-dev.lock
cd frontend
npm ci --ignore-scripts
```

本地依赖验证：在独立 Python 3.11 工具环境中按完整哈希下载 wheels、离线安装测试锁，`pip check` 返回 `No broken requirements found`。这项结果只证明当次本地安装一致性，不替代 PostgreSQL、HTTP、浏览器、Linux 容器或业务测试。

## 必要修改

| 包 | 原安装清单 | 本案例 | 理由 |
|---|---|---|---|
| FastAPI | 0.115.6 | 0.136.3 | 统一到原有锁版本，允许当前锁定 Starlette；不继续保留安装清单与锁不同版本 |
| python-multipart | 0.0.20 | 0.0.31 | 同系列修复；包含表单解析公告修复。特定 parse_form 公告不应直接归因于 FastAPI MultipartParser 调用 |
| PyJWT | 2.10.1（旧锁2.13.0） | 2.14.0 | 默认 decode 的未可信 header JSON 深嵌套异常修复；不只因 HS256/无 JWK 就排除此问题 |
| cryptography | 44.0.0（旧锁49.0.0） | 48.0.1 | 使用修复 wheel 内嵌 OpenSSL 的最低候选；当前业务使用 Fernet，未为未调用的 X509/PKCS7 API 无依据升到50 |
| axios | 1.7.9 | 1.20.0 | 同major安全修复，官方 fetch-adapter 公告修复版本1.20.0 |
| Vite | 6.0.3 | 6.4.3 | 同major修复 dev server 文件读取/Windows路径公告 |
| react-router-dom | 6.28.0 | 6.30.6 | 同major修复已提供补丁的重定向公告；保留以下残余 |
| ECharts | 5.5.1 | 5.5.1 | 当前图表为line/pie/radar，无受影响的lines series，保留现有显示契约 |
| pytest | 8.3.4 | 8.3.4 | 保留测试契约；测试需隔离容器及独占basetemp，见下文 |

前端通过 overrides 固定 browserslist 4.28.7、baseline-browser-mapping 2.11.0、postcss 8.5.23、nanoid 3.3.18、source-map-js 1.2.2，修复对应锁定的传递依赖公告；原 React/Ant Design/TypeScript 等直接版本保留。

## 真实审计结果与残余公告

原前端 npm audit 返回12项（9 high、3 moderate）；修改后返回3项 moderate，0 high/critical，退出码仍为1。3项是依赖条目计数，不是3个相互独立漏洞。本项目没有声明“0漏洞”。

| 公告 | 当前判断与边界 | 处理 |
|---|---|---|
| [ECharts GHSA-fgmj-fm8m-jvvx](https://github.com/advisories/GHSA-fgmj-fm8m-jvvx) | 条件为lines series + tooltip +未自定义formatter +未可信data.name。当前页面仅line、pie、radar，源码未发现该组合 | 保留5.5.1；添加该组合前升到修复版6.1.0并做显示回归 |
| [Router GHSA-wrjc-x8rr-h8h6](https://github.com/remix-run/react-router/security/advisories/GHSA-wrjc-x8rr-h8h6) | 官方修复7.18.0；当前导航为固定站内路径/固定profiles前缀，未把未可信输入作为完整跳转URL | 保留6.30.6；不新增可控redirect，未来相关功能先升级与测试 |
| [Router GHSA-337j-9hxr-rhxg](https://github.com/remix-run/react-router/security/advisories/GHSA-337j-9hxr-rhxg) | 公告明确 Declarative Mode 不受影响；本案例 BrowserRouter/Routes/Route，未做SSR hydration | 保留6系列；SSR/Data Mode引入时重新核对 |
| [pytest GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g) | Unix共享/tmp的pytest-of目录，本地用户攻击条件；本地Windows工具与隔离测试容器不同 | 保留8.3.4；测试容器不共享/tmp，显式独占`--basetemp=/tmp/jiuye-case-pytest`；共享主机改用自己拥有的独占目录或修复版本 |
| [cryptography GHSA-g6cj-pr64-35w5](https://github.com/pyca/cryptography/security/advisories/GHSA-g6cj-pr64-35w5) | PKCS#7 EnvelopedData解密oracle；当前core/crypto.py只用Fernet encrypt/decrypt | 保留48.0.1；启用该API前需修复版50及回归 |
| cryptography X509公告 GHSA-m2h6-j472-rp4c、GHSA-jwv3-5hgf-82ww | PyPI48.0.1元数据仍标记；上游affected范围字段与PyPI标记有差异，且业务模块未用X509验证 | 保留差异，不擅自把元数据标记改为PASS；启用相关API前核对当前官方修复 |
| [PyJWT GHSA-42vr-xj54-vc7v](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-42vr-xj54-vc7v)、[GHSA-x33g-cr3x-6449](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-x33g-cr3x-6449) | 2.14.0仍有元数据记录，分别涉及PyJWKClient/关闭签名验证、OKP JWK；当前固定HS256、签名验证开启、无JWK客户端/OKP | 保留2.14.0；启用相关API前升到修复2.15.0并回归 |

这些适用性判断来自相关模块/六个管理页面的定点静态核对，不是全仓漏洞审计、运行渗透测试或长期安全保证。公告库仅截至查询时，必须定期按实际依赖变化重新核对。

必要升级的官方依据：[Vite6.4.3](https://github.com/vitejs/vite/security/advisories/GHSA-fx2h-pf6j-xcff)、[axios1.20.0](https://github.com/axios/axios/security/advisories/GHSA-vh66-26gq-q6x8)、[Router6.30.6](https://github.com/remix-run/react-router/security/advisories/GHSA-jjmj-jmhj-qwj2)、[PyJWT2.14.0默认header异常](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-8wjv-2p76-3863)、[cryptography48.0.1 OpenSSL wheels](https://github.com/pyca/cryptography/security/advisories/GHSA-537c-gmf6-5ccf)。

## 许可与分发范围

完整逐版本许可清单见 [licenses/INDEX.md](../licenses/INDEX.md) 与 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。共272个包/版本（包括跨平台可选项），全部发行物完整哈希已验证；218项直接捕获许可/NOTICE、2项从发行物README捕获许可、51项原生平台包关联同版本主包许可、1项仅发布者MIT元数据声明。没有将这些不同证据状态批量改为PASS。

对应版本证据解决“声明来自哪个版本”的问题，不替代自有权利确认，也不把LGPL/MPL/CC-BY等第三方材料改授权Apache-2.0。源码引用包与分发dist/镜像是不同范围；本次不分发包本体或构建资产。toggle-selection许可全文仍需在未来分发含它的产物前补齐。
