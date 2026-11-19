from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import client_packages as packages
import build
import hashlib
from release import expected, fingerprint, write


class ClientPackagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "client/main").mkdir(parents=True)
        shutil.copyfile(packages.ROOT / "client/main/default-hub.cjs", self.root / "client/main/default-hub.cjs")
        write(self.root / "client/package.json", {"version": "1.13.19"})
        self.env = {**os.environ, "GITHUB_REPOSITORY": "example/project", "PUBLIC_RELEASE_REPOSITORY": "example/project",
                    "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/build-default-hub-1.13.19",
                    "GITHUB_REF_PROTECTED": "true", "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123",
                    "SODUWORK_ARTIFACT_LABEL": "cn", "SODUWORK_DEFAULT_HUB_URL": "https://hub.example.test/"}
        self.ctx = packages.package_context(self.env, self.root)

    def test_default_and_custom_builds_keep_product_version_and_source_identity(self):
        self.assertEqual(self.ctx["defaultHubUrl"], "https://hub.example.test")
        self.assertEqual(self.ctx["artifactLabel"], "cn")
        self.assertEqual(self.ctx["version"], "1.13.19")
        self.assertEqual(self.ctx["commit"], "a" * 40)
        for value in [None, ""]:
            env = {**self.env, "SODUWORK_DEFAULT_HUB_URL": value}
            if value is None:
                del env["SODUWORK_DEFAULT_HUB_URL"]
            self.assertEqual(packages.package_context(env, self.root)["defaultHubUrl"], "https://service.soduwork.ai")
        self.assertNotIn("tag", self.ctx)

    def test_untrusted_build_identity_labels_and_urls_fail(self):
        for key, value in [("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REF_PROTECTED", "false"),
                           ("PUBLIC_RELEASE_REPOSITORY", "other/repo"), ("GITHUB_REF", "refs/tags/v1.13.19"),
                           ("GITHUB_SHA", "short"), ("GITHUB_RUN_ID", "0"), ("SODUWORK_ARTIFACT_LABEL", "../cn"),
                           ("SODUWORK_ARTIFACT_LABEL", "cn\ncommand"), ("SODUWORK_ARTIFACT_LABEL", ""),
                           ("SODUWORK_DEFAULT_HUB_URL", "https://user:secret@hub.example.test"),
                           ("SODUWORK_DEFAULT_HUB_URL", "https://hub.example.test/api")]:
            with self.subTest(key=key), self.assertRaises(ValueError) as caught:
                packages.package_context({**self.env, key: value}, self.root)
            self.assertNotIn("secret", str(caught.exception))

    def test_existing_binary_review_and_reviewer_gate_are_required(self):
        for rules, success in [([], False), ([{"type": "required_reviewers", "reviewers": []}], False),
                               ([{"type": "required_reviewers", "reviewers": [{"type": "User"}]}], True)]:
            def api(route):
                return {"private": False} if route == "" else {"protection_rules": rules}
            with patch.object(packages, "api", side_effect=api), patch.object(packages, "policy") as policy:
                if success:
                    packages.preflight(self.ctx, self.root)
                    policy.assert_called_once_with(self.ctx, self.root)
                else:
                    with self.assertRaisesRegex(ValueError, "required reviewers"):
                        packages.preflight(self.ctx, self.root)

    def candidate(self):
        folder = self.root / "packages"
        folder.mkdir()
        for name in expected(self.ctx, "windows"):
            (folder / name).write_bytes(b"synthetic accepted installer")
        checks = {"ok": True, "sourceCommit": self.ctx["commit"], "pipelineId": self.ctx["runId"],
                  "defaultHubUrl": self.ctx["defaultHubUrl"], "bundledReleaseHistorySha256": "b" * 64,
                  "artifacts": [fingerprint(p) for p in folder.iterdir()]}
        return folder, checks

    def test_receipt_retains_original_installer_names_and_deployment_identity(self):
        folder, checks = self.candidate()
        result = packages.record_packages(self.ctx, "windows", folder, checks)
        self.assertTrue(result["deploymentRequired"])
        self.assertEqual(result["artifacts"][0]["name"], "SoduWork-1.13.19-Setup.exe")
        self.assertEqual(result["defaultHubUrl"], self.ctx["defaultHubUrl"])
        self.assertTrue((folder / "client-packages.json").is_file())

    def test_other_environment_build_or_replaced_bytes_cannot_be_registered_as_accepted(self):
        folder, checks = self.candidate()
        for key, value in [("ok", False), ("sourceCommit", "b" * 40), ("pipelineId", "124"),
                           ("defaultHubUrl", "https://other.example.test"), ("bundledReleaseHistorySha256", "")]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                packages.record_packages(self.ctx, "windows", folder, {**checks, key: value})
        original = copy.deepcopy(checks)
        (folder / expected(self.ctx, "windows")[0]).write_bytes(b"replaced installer")
        with self.assertRaisesRegex(ValueError, "different installer bytes"):
            packages.record_packages(self.ctx, "windows", folder, original)
        self.assertFalse((folder / "client-packages.json").exists())

    def test_linux_packages_still_require_signing_acceptance(self):
        folder, checks = self.candidate()
        with self.assertRaisesRegex(ValueError, "Linux package signing"):
            packages.record_packages(self.ctx, "linux", folder, checks)

    def test_on_demand_native_build_preserves_committed_release_history(self):
        folder, checks = self.candidate()
        history = self.root / "client/main/release-history.json"
        history.write_bytes(b'{"versions":[]}\n')
        write(self.root / "tools/ci/soduwork-acceptance-baselines.json", {})
        write(self.root / "release-evidence/windows/acceptance.json", checks)
        dist = self.root / "client/dist"
        dist.mkdir()
        name = expected(self.ctx, "windows")[0]
        shutil.move(folder / name, dist / name)
        with patch.object(build, "ROOT", self.root), patch.object(build, "package_native", return_value={}), \
                patch.object(build, "first_release", return_value=True), patch.object(build, "run"), \
                patch("release_notes.bundle") as bundle:
            result = build.native(self.ctx, "windows", folder, self.root, preserve_release_history=True)
        bundle.assert_not_called()
        self.assertEqual(result["bundledReleaseHistorySha256"], hashlib.sha256(history.read_bytes()).hexdigest())
        self.assertNotIn("releaseNotesSha256", result)
        self.assertEqual((folder / name).read_bytes(), (dist / name).read_bytes())

    def test_workflow_only_uploads_actions_artifacts_and_reuses_native_quality_gates(self):
        workflow = (packages.ROOT / ".github/workflows/client-packages.yml").read_text()
        for text in ["workflow_dispatch:", "environment: public-release", "needs: [preflight, quality, security]",
                     "./.github/workflows/ci.yml", "./.github/workflows/security.yml", "inputs.default_hub_url",
                     "actions/upload-artifact@", "client-${{ needs.preflight.outputs.version }}-${{ needs.preflight.outputs.label }}"]:
            self.assertIn(text, workflow)
        for text in ["contents: write", "release.py draft", "RELEASE_PASSWORD"]:
            self.assertNotIn(text, workflow)
        for line in workflow.splitlines():
            if "run:" in line:
                self.assertNotIn("inputs.", line)


if __name__ == "__main__":
    unittest.main()
