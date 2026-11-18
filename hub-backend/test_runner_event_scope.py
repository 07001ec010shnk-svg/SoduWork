"""Local execution routing stays separate from Hub retention and pressure."""
import unittest
import tempfile
from unittest.mock import patch, AsyncMock

from app.services import dispatch_identity, runner_event_scope
from app.services.runner_admission import AdmissionQueue, INTERACTIVE, MANUAL


class ScopeTests(unittest.TestCase):
    def test_legacy_requires_exact_desktop_identity_and_explicit_hub_wins(self):
        run = {"taskId": "chat-synthetic", "cli": "opencode"}
        ref = dispatch_identity.execution_ref("chat:chat-synthetic:opencode")
        self.assertTrue(runner_event_scope.is_local(run, ref))
        self.assertFalse(runner_event_scope.is_local(run, "arbitrary-hub-ref"))
        self.assertFalse(runner_event_scope.is_local({**run, "eventDestination": "hub"}, ref))
        self.assertFalse(runner_event_scope.is_local({**run, "eventDestination": "unknown"}, ref))
        for invalid in ("", None, False):
            self.assertFalse(runner_event_scope.is_local({**run, "eventDestination": invalid}, ref))
        self.assertTrue(runner_event_scope.is_local({"eventDestination": "local"}, "new-ref"))

    def test_bindings_only_include_confirmed_local_tasks(self):
        runs = {"local": {"eventDestination": "local", "taskId": "chat-a"},
                "hub": {"eventDestination": "hub", "taskId": "chat-b"},
                "native": {"eventDestination": "local"}}
        self.assertEqual(runner_event_scope.local_bindings(runs), {"local": "chat-a"})

    def test_ingress_overwrites_forged_routing_and_internal_flags(self):
        message = {"_eventDestination": "local", "_localParent": True,
                   "_admitted": True, "_admissionKey": "forged",
                   "ctx": {"eventDestination": "local"}}
        hub = runner_event_scope.ingress(message, local=False)
        self.assertEqual(hub["_eventDestination"], "hub")
        self.assertFalse(hub.get("_localParent"))
        self.assertFalse(hub.get("_admitted"))
        self.assertNotIn("_admissionKey", hub)
        self.assertEqual(runner_event_scope.ingress(message, local=True)["_eventDestination"], "local")

    def test_hub_pressure_skips_hub_interactive_but_keeps_resource_gates(self):
        queue = AdmissionQueue()
        queue.submit("hub", "hub", INTERACTIVE, "dispatch", {"_eventDestination": "hub"})
        queue.submit("local", "local", MANUAL, "dispatch", {"_eventDestination": "local"})
        self.assertIsNone(queue.claim_next(0, 2, False, hub_blocked=True))
        self.assertIsNone(queue.claim_next(2, 2, True, hub_blocked=True))
        self.assertEqual(queue.claim_next(0, 2, True, hub_blocked=True).key, "local")
        self.assertIsNone(queue.claim_next(0, 2, True, hub_blocked=True))
        self.assertEqual(queue.claim_next(0, 2, True).key, "hub")

    def test_unattributed_interactive_is_blocked(self):
        queue = AdmissionQueue()
        queue.submit("unknown", "unknown", INTERACTIVE, "dispatch", {"dispatchId": "chat:forged"})
        self.assertIsNone(queue.claim_next(0, 2, True, hub_blocked=True))

    def test_legacy_local_queue_payload_survives_restart_under_hub_pressure(self):
        from app.services.runner_admission_journal import Journal
        with tempfile.TemporaryDirectory() as root:
            queue = AdmissionQueue(journal=Journal(root, "synthetic-runner"))
            queue.submit("explicit-hub", "hub", INTERACTIVE, "dispatch",
                         {"_localParent": True, "_eventDestination": "hub"})
            queue.submit("old-local", "local", INTERACTIVE, "dispatch", {"_localParent": True, "op": "job.dispatch"})
            restored = AdmissionQueue(journal=Journal(root, "synthetic-runner"))
            claimed = restored.claim_next(0, 2, True, hub_blocked=True)
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.key, "old-local")
            self.assertEqual(runner_event_scope.payload_destination(claimed.payload), "local")
            self.assertIsNone(restored.claim_next(0, 2, True, hub_blocked=True))

    def test_replay_scope_only_falls_back_when_explicit_destination_absent(self):
        self.assertEqual(runner_event_scope.payload_destination({"_localParent": True, "op": "job.dispatch"}), "local")
        for explicit in ("hub", "unknown", "", None):
            self.assertEqual(runner_event_scope.payload_destination(
                {"_localParent": True, "_eventDestination": explicit}), "hub")
        self.assertEqual(runner_event_scope.payload_destination({}), "hub")

    def test_legacy_comments_need_confirmed_target_ownership(self):
        queue = AdmissionQueue()
        legacy = {"_localParent": True, "op": "job.comment"}
        queue.submit("hub-comment", "hub-ref", INTERACTIVE, "comment", legacy)
        queue.submit("local-comment", "local-ref", INTERACTIVE, "comment", legacy)
        self.assertEqual(runner_event_scope.payload_destination(legacy), "hub")
        claimed = queue.claim_next(0, 2, True, hub_blocked=True, local_refs={"local-ref"})
        self.assertEqual(claimed.key, "local-comment")
        self.assertIsNone(queue.claim_next(0, 2, True, hub_blocked=True, local_refs={"local-ref"}))

    def test_comment_explicit_hub_wins_even_for_confirmed_local_target(self):
        queue = AdmissionQueue()
        queue.submit("hub-comment", "local-ref", INTERACTIVE, "comment",
                     {"_localParent": True, "op": "job.comment", "_eventDestination": "hub"})
        self.assertIsNone(queue.claim_next(0, 2, True, hub_blocked=True, local_refs={"local-ref"}))

    def test_routing_survives_metadata_restart_and_queue_restart(self):
        from app.services import local_base
        from app.services.runner_admission_journal import Journal
        with tempfile.TemporaryDirectory() as root, patch.object(local_base, "_STORE", root), \
                patch.dict(local_base._RUNS, {}, clear=True):
            local_base._RUNS["synthetic-ref"] = {"cli": "opencode", "taskId": "chat-test",
                                                 "eventDestination": "local", "state": "done"}
            local_base._save_meta("synthetic-ref")
            local_base._RUNS.clear()
            local_base.reclaim()
            self.assertTrue(runner_event_scope.is_local(local_base._RUNS["synthetic-ref"], "synthetic-ref"))
            queue = AdmissionQueue(journal=Journal(root, "synthetic-runner"))
            queue.submit("local", "synthetic-ref", INTERACTIVE, "comment", {"_eventDestination": "local"})
            restored = AdmissionQueue(journal=Journal(root, "synthetic-runner"))
            self.assertEqual(restored.claim_next(0, 2, True, hub_blocked=True).key, "local")

    def test_snapshots_skip_local_before_the_bounded_window(self):
        import runner_main
        from app.services import ai_local
        machine = runner_main.Runner.__new__(runner_main.Runner)
        machine._agent_inventory, machine._session_marks = [], {}
        runs = {f"local-{index}": {"taskId": "chat-local", "cli": "opencode",
                                  "eventDestination": "local", "state": "done", "seq": 1}
                for index in range(20)}
        runs["hub"] = {"taskId": "chat-hub", "cli": "opencode", "eventDestination": "hub",
                       "state": "done", "seq": 1}
        with patch.dict(ai_local._RUNS, runs, clear=True), \
                patch.object(ai_local, "console_list_sync", return_value={
                    "runs": [{"ref": ref, **run} for ref, run in runs.items()]}), \
                patch.object(ai_local, "session_sync", return_value={"seq": 1, "status": "done", "items": []}):
            snapshots, marks = machine._session_snapshots()
        self.assertEqual([snapshot["ref"] for snapshot in snapshots], ["hub"])
        self.assertEqual([mark[0] for mark in marks], ["hub"])


class IngressTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_transport_boundaries_replace_caller_scope(self):
        import runner_main
        machine = runner_main.Runner.__new__(runner_main.Runner)
        machine._job, machine._send = AsyncMock(return_value={"ok": True}), AsyncMock()
        forged = {"op": "job.dispatch", "id": "synthetic", "_eventDestination": "local",
                  "_admitted": True, "_localParent": True}
        await machine._on(forged)
        self.assertEqual(machine._job.await_args.args[0]["_eventDestination"], "hub")
        self.assertFalse(machine._job.await_args.args[0].get("_admitted"))
        await machine._local_rpc_dispatch({**forged, "_eventDestination": "hub"})
        self.assertEqual(machine._job.await_args.args[0]["_eventDestination"], "local")
        self.assertFalse(machine._job.await_args.args[0].get("_admitted"))


if __name__ == "__main__":
    unittest.main()
