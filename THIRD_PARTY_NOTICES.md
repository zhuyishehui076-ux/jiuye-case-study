# 第三方许可与归属

JIUYE 自有源码、文档及本案例新写的合成示例按项目根 `LICENSE` 的 Apache-2.0 条款提供；第三方包、其数据、归属和附带许可保留各自条款。本文件不重新授权第三方材料，也不声明全体依赖与项目已经完成整体法律兼容性认证。

本次分发的是源码、依赖锁、部署模板、合成示例和许可证据文本。下载发行物、安装环境、真实数据、旧 Git 历史、前端 dist 与 Docker 镜像不随源码分发。将来若发布这些产物，应按产物实际内容补齐分发许可和归属。

## 已保留的证据

完整逐版本清单见 [licenses/INDEX.md](licenses/INDEX.md)，运行/测试锁与安全公告见 [docs/dependencies.md](docs/dependencies.md)。

- Apache-2.0 包（包括 ECharts、TypeScript 与 baseline-browser-mapping）发行物附带的 LICENSE/NOTICE 在对应版本目录保存。修改第三方文件时须追加修改说明；本案例没有修改第三方包代码。
- MIT、ISC、BSD 等包的原版权、许可及免责声明保留在对应文本中，未用项目 LICENSE 替换。
- caniuse-lite 的浏览器兼容数据声明 CC-BY-4.0，上游数据来源为 [caniuse.com](https://caniuse.com/)；许可见 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。该数据作为工具依赖引用，未修改或将数据改标 Apache-2.0。
- certifi 的 MPL-2.0、psycopg/psycopg-binary 的 LGPLv3，以及 Python、NumPy、lxml 等发行物中的多重/附带许可保留。它们仍受各自条款约束。
- 51 个 esbuild/Rollup 平台二进制发行包没有内嵌 LICENSE 文件。每个包的官方精确版本声明、完整发行物哈希已核对，并关联同版本主包的许可文本；此状态明确区别于在平台发行物中直接发现许可。这里不分发这些二进制。
- toggle-selection 1.0.6 的官方精确版本元数据声明 MIT；该发行物和 README 未附许可全文。按发布的 gitHead 查找 LICENSE 返回 404 后停项，没有猜写作者/版权或把缺口改成通过。本仓库仅引用其包，不复制或分发包内容；将来分发含它的构建产物前需补齐该文本或移除/替换依赖。

原始许可中的第三方作者姓名、公开联系地址和版权声明属于归属文本，不是本项目的用户数据。它们不得被误写为项目作者或背书。
