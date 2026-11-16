# Third-party distribution materials

[简体中文](README.zh-CN.md) · **English** · [Notices](../NOTICE.md)

See [MIT review boundaries](mit-review.en.md) for file-level provenance checks,
license scope and local validation builds. Original third-party copyrights and
licenses remain unchanged.

This directory retains verified license texts and source evidence. Project MIT terms do not override third-party terms. Hashes in `manifest.json` use UTF-8/LF. Review the owner, terms, exact version, and actual artifact when updating evidence; changing a hash alone is insufficient.

## Build integration

- Desktop beforePack checks license snapshots and dependency lock versions, collecting original notices for copied Pi/SSH packages. Electron's LICENSE and Chromium notices remain supplied by electron-builder.
- When a package places its license in README, retain the complete text. Missing original material needs reviewed fixed-version evidence; an unknown omission fails the build.
- Runner retains dependency metadata and wheel-provided licenses. Hub retains this directory and the project license. Bundled skills include project-owned guides and the reviewed upstream development skills declared in `tools/distribution/PUBLIC-SOURCE.json`. Each imported skill retains its original license, fixed-version provenance, and any adaptation notes; see [skill notices](../skills/THIRD-PARTY.md).
- These checks do not constitute a complete SBOM, vulnerability scan, or legal certification.

## Runner certificate bundle

The standalone Runner ships the unmodified Mozilla CA bundle from
[`certifi` 2026.10.23](https://pypi.org/project/certifi/2026.10.23/) (MPL-2.0).
`runner.spec` preserves its original distribution license and `cacert.pem`.
macOS WSS connections add this bundle to the default trust context, retaining
explicitly configured private CAs and hostname verification. Public Mac builds
check both isolated TLS handshakes and the CA data/loader in the frozen Runner.

## Python dependencies in Hub and Runner

The Hub image installs its Python dependencies with pip from `hub-backend/requirements.txt`; the standalone Runner uses `hub-backend/requirements-runner.txt`. The Hub retains installed `*.dist-info` license files, and `runner.spec` copies dependency metadata and original notices into the frozen Runner.

**Pillow**: the Hub uses it to validate and process images, including user avatars (validated and cropped to 256×256), format checks for AI-generated images (PNG, JPEG, WEBP), and file library thumbnails. The Runner also uses it to verify the dimensions and decoded content of raster images embedded in SVG attachments.

- Project: https://python-pillow.github.io/
- Source: https://github.com/python-pillow/Pillow
- Version: 12.x (`hub-backend/requirements.txt` requires `>=12.3,<13`; the installed version in the image is authoritative)
- License: MIT-CMU. Copyright Secret Labs AB, Fredrik Lundh, Jeffrey A. Clark and contributors.

The MIT-CMU license requires the copyright and permission notices to be retained, and the authors' names may not be used in advertising for the distribution without prior written permission. SoduWork does not modify Pillow; the original license stays in Pillow's `dist-info` directory in the image. Pillow wheels also bundle image libraries such as libjpeg, libpng, zlib, libwebp and FreeType, whose licenses are included in the same directory's license file.

**resvg_py 0.5.0**: the Runner uses this MIT-licensed Python binding to rasterize SVG attachments locally for supported non-Pi agents. Its wheel retains the wrapper license but omits notices for statically linked Rust libraries. `licenses/resvg_py-0.5.0-NOTICES.txt` therefore retains the original grants and provenance from the exact PyPI source distribution, checksum-verified packages in its Cargo.lock, and explicitly referenced upstream license materials, including resvg/usvg 0.48.1, tiny-skia 0.12.0 and their dependencies. This conservative inventory includes build and platform dependencies; it does not assert that every listed package is present in every wheel. Original dual-license options and additional third-party grants remain intact.

The exact-version `pythonNoticeOverrides` entry makes `runner.spec` include this aggregate notice and reject a different installed resvg_py version until its notices are reviewed. Dependency metadata collection alone cannot discover Rust dependencies inside a Python extension. Rebuilding with other crate versions or updating the Python wheel requires a new artifact and notice review; GitHub's current branch is not evidence for the published 0.5.0 wheel. Fonts supplied by the execution machine are not copied into this distribution.

The aggregate records one historical evidence limit: the original Getreuer GaussianIIR author page confirms the Simplified BSD option, but its 2011 ZIP could not be recovered. The 2013 author notices are explicitly supplemental, not a substitute for that missing archive. Source review and internal validation do not establish complete notice coverage for a public binary release; that gap remains part of its artifact review.

## Outstanding binary-release work

1. **proxy-agent-negotiate 1.1.0:** the exact npm release and tagged source declare MIT; the release owner reviewed and accepted that declaration for binary distribution. Evidence is retained in `licenses/proxy-agent-negotiate-upstream-evidence.txt`. Pi 1.4.0 no longer includes the old native clipboard package.
2. **Windows shell corresponding source:** the version-specific acquisition checklist is documented in [windows-shell-corresponding-source.md](windows-shell-corresponding-source.md), currently in Chinese. Physically archive and verify source, patches, and build material for the exact PortableGit/MSYS2/Bash/7-Zip artifacts distributed. A checklist or upstream URL is not evidence that a particular release has completed this work.
3. **Platform inventories:** `native-artifacts.json` is a static source-tree inventory. Final Windows, macOS, and Linux packages require per-artifact records and notices from their own builds.
4. **Icons and trademarks:** record asset provenance and distribution terms separately from product names and trademark use. Compatibility names do not imply endorsement.

The current single-host self-hosting recipe does not deploy Redis or Valkey. Any additional server components introduced by a downstream deployment need their own inventory and license review.

`public-release.json` distinguishes obtained source evidence from unresolved binary-release work. Installers and container images are published only after the items above are resolved for the exact artifacts.
