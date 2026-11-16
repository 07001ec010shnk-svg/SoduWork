# 第三方分发材料

**简体中文** · [English](README.md) · [依赖声明](../NOTICE.zh-CN.md)

本目录保留经过核验的许可原文和来源证据。项目 MIT 许可证不覆盖第三方内容。`manifest.json` 摘要按 UTF-8/LF 计算；更新时核对版权人、条款、固定版本和实际发行物，不能仅改摘要来通过检查。

逐文件来源核验、MIT 适用范围及本地验证构建方式见 [MIT 审查边界](mit-review.md)。第三方原始版权和许可证保持不变。

## 构建接入

- 客户端 beforePack 检查许可快照和锁定版本，收集实际复制的 Pi/SSH 依赖声明；Electron LICENSE 和 Chromium notices 由 electron-builder 保留。
- 许可位于 README 的组件保留全文；缺失原文需要经过审查的固定版本证据，未知缺失项阻断构建。
- Runner 保留 Python 依赖元数据和 wheel 内许可；Hub 保留本目录与项目 LICENSE。随附技能包含 `tools/distribution/PUBLIC-SOURCE.json` 声明的项目自有指南和已核对来源的上游开发技能。每个引入的技能保留原始许可、固定版本来源和适配记录，见[技能许可说明](../skills/THIRD-PARTY.zh-CN.md)。
- 这些检查不是完整 SBOM、漏洞扫描或法律许可认证。

## Runner 证书包

独立 Runner 携带 [`certifi` 2026.10.23](https://pypi.org/project/certifi/2026.10.23/)
提供的未修改 Mozilla CA 证书包，许可为 MPL-2.0。`runner.spec` 保留依赖原始许可及
`cacert.pem`。macOS 的 WSS 连接在默认信任上下文中加入这份证书包，保留显式配置的
私有 CA 和主机名校验。公开 Mac 构建同时检查隔离 TLS 握手和冻结 Runner 中的证书数据及加载模块。

## Hub 与 Runner 中的 Python 依赖

Hub 镜像构建时按 `hub-backend/requirements.txt` 用 pip 安装 Python 依赖；独立 Runner 使用 `hub-backend/requirements-runner.txt`。Hub 保留安装的 `*.dist-info` 原许可文件，`runner.spec` 把依赖元数据和许可原文复制进冻结 Runner。

**Pillow**：Hub 用它校验和处理图片，包括用户头像（校验后裁成 256×256）、AI 生成图片的格式校验（PNG、JPEG、WEBP）和文件库缩略图。Runner 也用它核验 SVG 附件内嵌栅格图片的尺寸及解码内容。

- 项目：https://python-pillow.github.io/
- 源码：https://github.com/python-pillow/Pillow
- 版本：12.x（`hub-backend/requirements.txt` 限定 `>=12.3,<13`，以镜像实际安装版本为准）
- 许可：MIT-CMU。版权归 Secret Labs AB、Fredrik Lundh、Jeffrey A. Clark 及贡献者所有。

MIT-CMU 许可要求保留版权和许可声明，并且未经书面许可，不得用作者的名字为相关分发做宣传。SoduWork 不修改 Pillow，许可原文保留在镜像中 Pillow 的 `dist-info` 目录。Pillow 的 wheel 还捆绑了 libjpeg、libpng、zlib、libwebp、FreeType 等图像库，这些库的许可同样收录在该目录的许可文件中。

**resvg_py 0.5.0**：Runner 使用这个 MIT 许可的 Python 绑定，在本地将 SVG 附件栅格化供支持的非 Pi 底座使用。它的 wheel 仅保留绑定自身许可，缺少静态链接 Rust 库的声明。因此 `licenses/resvg_py-0.5.0-NOTICES.txt` 从精确版本的 PyPI 源码归档、其 Cargo.lock 锁定且校验和已验证的 crate 包，以及源码明确引用的上游许可材料保留原始授权与来源，包括 resvg/usvg 0.48.1、tiny-skia 0.12.0 及其依赖。这份保守清单也包括构建和平台依赖，不表示每个 wheel 都实际包含全部列出的包。上游双许可证选项及附加第三方授权原文均保留。

精确版本的 `pythonNoticeOverrides` 条目让 `runner.spec` 携带这份聚合声明；安装的 resvg_py 版本不同且尚未重新审核时会阻止构建。仅复制 Python 依赖元数据无法发现扩展内部的 Rust 依赖。改用其他 crate 版本构建或更新 Python wheel 后，必须重新审核实际发行物及声明，不能以 GitHub 当前分支替代已发布 0.5.0 wheel 的证据。执行机提供的字体不复制进本发行物。

聚合文件记录了一项历史证据限制：Getreuer GaussianIIR 原作者页面明确允许选择 Simplified BSD，但未能取得其 2011 年 ZIP。2013 年作者声明明确标为补充材料，不能替代缺失归档。本次源码审查与内部验证不代表公开二进制的声明覆盖已完整；该缺口仍需纳入实际发行物审核。

## 二进制发行尚需完成

1. **proxy-agent-negotiate 1.1.0**：固定版本的 npm 包和上游标签声明 MIT，发布负责人已审核并接受该声明用于二进制分发；证据保留在 `licenses/proxy-agent-negotiate-upstream-evidence.txt`。Pi 1.4.0 已移除旧版原生剪贴板依赖。
2. **Windows 外壳对应源码**：[获取清单](windows-shell-corresponding-source.md)已记录版本和核验步骤；具体发行物仍须实际归档 PortableGit/MSYS2/Bash/7-Zip 对应源码、补丁和构建材料，并核对摘要。清单或上游链接不代表某个安装包已完成该项。
3. **各平台最终清单**：`native-artifacts.json` 是源树静态盘点；Windows、macOS、Linux 最终安装包均须由各自构建生成逐件记录和许可材料。
4. **图标和商标**：分开记录图形来源、分发许可与商标使用边界；兼容服务名称不表示官方认证。

当前单机自托管配方不部署 Redis 或 Valkey。下游新增服务组件需自行登记依赖和审查许可。

`public-release.json` 区分已取得的源码证据与尚未完成的二进制发行材料。安装包和容器镜像须针对实际发行物完成上述各项后再发布。
