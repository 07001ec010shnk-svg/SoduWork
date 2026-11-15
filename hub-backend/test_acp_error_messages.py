"""ACP storage guidance is specific and preserves native diagnostic evidence."""
import io
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.services import acp_bridge as bridge


class AcpErrorMessagesTests(unittest.TestCase):
    raw = (r"Internal error: turn failed: MoveFileExW EXDEV (Win32 17): "
           r"D:\private\.dsh\sessions\project\session\session.v4.jsonl.zstd.abc123.tmp"
           r" -> D:\private\.dsh\sessions\project\session\session.v4.jsonl.zstd")

    def test_known_dsh_storage_error_is_actionable_and_keeps_original(self):
        for message in (self.raw, self.raw.replace("\\", "/"),
                        self.raw.replace(".zstd", "")):
            with self.subTest(message=message):
                result = bridge._friendly_bridge_error("dsh", message)
                self.assertIn("无法保存本机会话归档", result)
                self.assertIn("本次执行未完成", result)
                self.assertIn("重新登录或放宽工具权限不能解决", result)
                self.assertTrue(result.endswith("原始信息：" + message))

    def test_other_agents_keep_their_native_error(self):
        for agent in ("qwen", "opencode", "codex-harness", "acp", "dsh-other"):
            with self.subTest(agent=agent):
                self.assertEqual(bridge._friendly_bridge_error(agent, self.raw), self.raw)

    def test_unrelated_paths_and_error_codes_are_not_classified(self):
        cases = [self.raw.replace("session.v4.jsonl", name) for name in (
            "auth.json", "session.v3.jsonl", "session.v4.jsonl-other", "workspace.json")]
        cases += [self.raw.replace("EXDEV (Win32 17)", name) for name in (
            "EACCES (Win32 5)", "ENOENT (Win32 2)", "EXDEV", "EXDEV (Win32 117)")]
        cases.append(self.raw.replace("MoveFileExW", "rename"))
        for message in cases:
            with self.subTest(message=message):
                self.assertEqual(bridge._friendly_bridge_error("dsh", message), message)

    def test_authentication_guidance_still_preserves_original(self):
        message = "Authentication required: Use Qwen Code CLI to authenticate first."
        result = bridge._friendly_bridge_error("qwen", message)
        self.assertIn("qwen 缺少可用的模型凭据", result)
        self.assertTrue(result.endswith("原始信息：" + message))

    def test_unknown_error_is_unchanged(self):
        message = "Internal error: upstream unavailable"
        self.assertEqual(bridge._friendly_bridge_error("dsh", message), message)

    def test_dsh_output_recovery_is_bounded_and_keeps_the_original_session(self):
        compacted = 'Compacted 4 history items (~32000 tokens).'
        for agent, reasons, busy, compact_reply, resumes, succeeds in [
            ('dsh', ['max_tokens', 'end_turn'], False, None, 1, True),
            ('dsh', ['max_tokens', 'max_tokens'], False, None, 1, False),
            ('dsh', ['max_tokens', 'max_tokens', 'end_turn'], False, compacted, 2, True),
            ('dsh', ['max_tokens'] * 3, False, compacted, 2, False),
            ('dsh', ['max_tokens'] * 2, False, 'No compactable history yet.', 1, False),
            ('dsh', ['max_tokens'] * 2, False, '⚠ Compaction cancelled.', 1, False),
            ('dsh', ['max_tokens'], True, compacted, 0, False),
            ('qwen', ['max_tokens'], False, None, 0, False),
        ]:
            with self.subTest(agent=agent, reasons=reasons, busy=busy, compact_reply=compact_reply), \
                    TemporaryDirectory() as folder:
                acp = bridge.AcpBridge([], folder, agent, 'workspace')
                seen = []
                stops = iter(reasons)

                def request(method, params):
                    if method == 'initialize':
                        return {'protocolVersion': 1}
                    if method == 'session/new':
                        if compact_reply is not None:
                            acp._update({'sessionUpdate': 'available_commands_update',
                                         'availableCommands': [{'name': 'compact'}]})
                        return {'sessionId': 'existing-tools'}
                    self.assertEqual(method, 'session/prompt')
                    seen.append(params)
                    if params['prompt'][0]['text'] == '/compact':
                        acp._update({'sessionUpdate': 'agent_message_chunk',
                                     'content': {'type': 'text', 'text': compact_reply}})
                        return {'stopReason': 'end_turn'}
                    return {'stopReason': next(stops)}

                with patch.object(acp, 'start'), patch.object(acp, 'request', side_effect=request), \
                        patch.object(acp.access, 'apply'), patch.object(acp.transition, 'busy', return_value=busy), \
                        patch.object(bridge.qwen_runtime, 'apply_model'), patch.object(bridge, '_emit') as emit:
                    if succeeds:
                        self.assertEqual(acp.run('Original synthetic task'), 0)
                    else:
                        with self.assertRaises(bridge.BridgeError):
                            acp.run('Original synthetic task')
                self.assertEqual({p['sessionId'] for p in seen}, {'existing-tools'})
                texts = [p['prompt'][0]['text'] for p in seen]
                expected = ['Original synthetic task'] + [bridge._DSH_OUTPUT_CONTINUATION] * resumes
                if compact_reply is not None and resumes:
                    expected.insert(2, '/compact')
                self.assertEqual(texts, expected)
                if compact_reply:
                    self.assertNotIn(compact_reply, str(emit.call_args_list))

    def test_real_bridge_entry_emits_failure_and_always_closes(self):
        args = SimpleNamespace(agent=["fixture"], cwd="fixture", agent_name="dsh",
                               permission="workspace", session="", terminal=False)
        agent = Mock(agent_name="dsh")
        agent.run.side_effect = bridge.BridgeError(self.raw)
        output = io.StringIO()
        with patch.object(bridge, "_parse", return_value=args), \
                patch.object(bridge, "_utf8_stdio"), \
                patch.object(bridge, "AcpBridge", return_value=agent), \
                patch.object(bridge.sys, "stdin", io.StringIO("synthetic")), \
                patch.object(bridge.sys, "stdout", output):
            code = bridge.main()
        self.assertEqual(code, 2)
        event = json.loads(output.getvalue())
        self.assertEqual(event["error"]["name"], "AcpBridgeError")
        self.assertEqual(event["error"]["message"], bridge._friendly_bridge_error("dsh", self.raw))
        agent.run.assert_called_once_with("synthetic")
        agent.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
