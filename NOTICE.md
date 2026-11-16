# Third-party notices

[简体中文](NOTICE.zh-CN.md) · **English** · [Documentation](docs/README.md)

Project-owned source uses the root MIT license. Third-party code, fonts, assets, and any separately imported skills retain their original terms. See `third-party/manifest.json`, `client/THIRD_PARTY_NOTICES.md`, and any component-specific LICENSE, UPSTREAM.md, or ADAPTATIONS.md.

Connector icon provenance is recorded in `client/assets/connector-brands/sources.json`. An asset license does not grant a third-party trademark endorsement.

The prompt-settlement fixture in `client/tests/dsh-acp-settlement.test.cjs` retains an excerpt from `@openma/deepseek-harness-acp` 0.4.40/0.4.41. Copyright 2026 The @openma/deepseek-harness-acp authors. The excerpt remains under Apache-2.0; see `third-party/licenses/deepseek-harness-acp-LICENSE.txt`. SoduWork's compatibility loader and surrounding tests are project-owned code.

Binary releases need the notices and applicable corresponding source for the actual bundled components. Outstanding materials are tracked in `third-party/public-release.json`; a passing source check does not certify binary distribution.

From the root:

```sh
node tools/check-third-party.cjs
```

Or run `npm run check:licenses` from `client/`. Checks verify fixed-version evidence, hashes, and build integration. Packaging collects original licenses for copied runtime dependencies. Runner retains installed Python metadata and licenses; Hub includes project and third-party materials.

See [distribution materials](third-party/README.md) for coverage and remaining work.
