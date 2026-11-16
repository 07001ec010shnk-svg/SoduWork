"""Execution receipts cannot replace confirmation that the trusted host exited."""
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app.services import access_transition, local_base, local_ctx, local_process, local_spool, local_stream


class ExecutionHostCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='soduwork-host-cleanup-')
        self.addCleanup(self.temp.cleanup)
        self.ref = 'isolated-execution-cleanup'
        self.spec = {'id': 'isolated', 'status': 'status'}
        self.owner = object()
        self.run = {'state': 'running', 'pid': 12345, 'stream': self.spec,
                    '_workerIdentity': self.owner, 'accessPolicy': {'mode': 'full'}}
        local_process._RUNS[self.ref] = self.run
        self.addCleanup(local_process._RUNS.pop, self.ref, None)
        self.stack = self.enterContext(ExitStack())
        self.stack.enter_context(patch.object(local_process, '_store_dir', return_value=self.temp.name))
        self.stack.enter_context(patch.object(local_process, '_save_meta'))
        self.kill = Mock(return_value=False)

    def join(self, proc=None):
        return local_process._join_execution_host(
            self.ref, self.spec, 0, self.kill, proc=proc,
            pid=self.run['pid'], owner=self.owner)

    def test_real_finished_agent_with_slow_host_shutdown_preserves_success(self):
        spec, launch = local_spool.prepare(self.temp.name,
            [sys.executable, '-c', "print('synthetic-answer-completed')"], self.temp.name)
        wrapper = Path(self.temp.name, 'slow_exit.py')
        release = Path(self.temp.name, 'release-host')
        wrapper.write_text(
            'import atexit, os, runpy, sys, time\n'
            'release = sys.argv[1]\n'
            'def await_release():\n'
            '    deadline = time.monotonic() + 45\n'
            '    while not os.path.exists(release):\n'
            '        if time.monotonic() >= deadline:\n'
            '            os._exit(91)\n'
            '        time.sleep(.01)\n'
            'atexit.register(await_release)\n'
            'sys.argv = sys.argv[2:]\n'
            'sys.path.insert(0, os.path.dirname(sys.argv[0]))\n'
            'runpy.run_path(sys.argv[0], run_name="__main__")\n', encoding='utf-8')
        proc = subprocess.Popen([sys.executable, str(wrapper), str(release), *launch[1:]],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def terminate():
            if proc.poll() is None:
                proc.kill()
        watchdog = threading.Timer(45, terminate)
        watchdog.daemon = True
        watchdog.start()
        worker = None
        result, errors = [], []
        done = threading.Event()
        def join():
            try:
                result.append(self.join(proc))
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        try:
            self.spec = spec
            self.run.update(pid=proc.pid, stream=spec, proc=proc)
            lines = []
            code, outcome = local_spool.follow(self.temp.name, spec,
                lambda stream, line, end: lines.append(line), proc=proc)
            self.assertEqual((code, outcome), (0, 'completed'))
            self.assertIn('synthetic-answer-completed', ''.join(lines))
            self.assertIsNone(proc.poll())
            worker = threading.Thread(target=join, daemon=True)
            worker.start()
            deadline = time.monotonic() + 10
            while not self.run.get('_hostCleanup') and not done.is_set() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIn('_hostCleanup', self.run, 'The joiner never entered host cleanup')
            start = time.monotonic()
            self.assertFalse(done.wait(3.25), 'Host cleanup failed before the controlled release')
            self.assertGreater(time.monotonic() - start, 3,
                'The fixture must exceed the former three-second false-failure deadline')
            self.assertIsNone(proc.poll())
            self.kill.assert_not_called()
            release.touch()
            self.assertTrue(done.wait(10), 'The joiner did not observe the released host exit')
            self.assertEqual(errors, [])
            self.assertEqual(result, [(True, '')])
            self.assertEqual(proc.wait(timeout=1), 0)
            self.assertNotIn('_hostCleanup', self.run)
        finally:
            release.touch()
            watchdog.cancel()
            terminate()
            proc.wait(timeout=5)
            if worker is not None:
                worker.join(timeout=5)

    def test_fast_exit_preserves_the_native_result_without_killing(self):
        proc = Mock(pid=12345, poll=Mock(return_value=0))
        self.assertEqual(self.join(proc), (True, ''))
        self.kill.assert_not_called()

    def test_abnormal_wrapper_exit_is_not_masked_by_successful_agent_receipt(self):
        proc = Mock(pid=12345, poll=Mock(return_value=42))
        self.assertEqual(self.join(proc),
            (True, 'Permission-controlled execution host exited abnormally'))

    def test_unconfirmed_kill_keeps_the_host_active_and_blocks_resume(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        self.run.update(pid=child.pid, proc=child)
        result = []
        with patch.object(local_process, '_HOST_EXIT_GRACE_S', .05):
            worker = threading.Thread(target=lambda: result.append(self.join(child)), daemon=True)
            worker.start()
            deadline = time.monotonic() + 3
            while not self.kill.called and time.monotonic() < deadline:
                time.sleep(.01)
            self.kill.assert_called_once_with(self.ref, expected_pid=child.pid, expected_proc=child)
            self.assertTrue(worker.is_alive())
            self.assertEqual(result, [])
            self.assertEqual(self.run['state'], 'running')
            self.assertIs(self.run['proc'], child)
            self.assertEqual(self.run['pid'], child.pid)
            self.assertEqual(self.run['error'], local_process._HOST_EXIT_ERROR)
            self.assertFalse(local_ctx.prepare_resume(self.ref, 'must not launch', {})['ok'])
            self.assertFalse(access_transition.apply(self.ref, {}, 0)['ok'])
            child.terminate()
            child.wait(timeout=3)
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [(True, local_process._HOST_EXIT_ERROR)])

    def test_a_successful_kill_request_still_requires_observed_exit(self):
        self.kill.return_value = True
        proc = Mock(pid=12345)
        polls = [None, None, None, 0]
        proc.poll.side_effect = lambda: polls.pop(0)
        with patch.object(local_process, '_HOST_EXIT_GRACE_S', 0):
            self.assertEqual(self.join(proc), (True, local_process._HOST_EXIT_ERROR))
        self.assertEqual(proc.poll.call_count, 4)

    def test_metadata_and_termination_exceptions_do_not_remove_the_exit_fence(self):
        self.kill.side_effect = OSError('synthetic termination failure')
        proc = Mock(pid=12345, poll=Mock(side_effect=[None, None, 0]))
        with patch.object(local_process, '_HOST_EXIT_GRACE_S', 0), \
                patch.object(local_process, '_save_meta', side_effect=OSError('synthetic disk failure')):
            self.assertEqual(self.join(proc), (True, local_process._HOST_EXIT_ERROR))
        self.assertEqual(proc.poll.call_count, 3)

    def test_permission_transition_after_join_observes_confirmed_exit_only(self):
        proc = Mock(pid=12345, poll=Mock(return_value=0))
        self.assertEqual(self.join(proc), (True, ''))
        self.run['_accessTransition'] = {'phase': 'ready'}
        self.assertTrue(access_transition.finish(self.ref, 0, ''))
        self.assertTrue(self.run['_accessTransition']['oldExited'])
        self.assertEqual(self.run['state'], 'access-changing')

    def test_cancelled_and_permission_transition_turns_do_not_get_cleanup_extension(self):
        for change in ({'stopping': True}, {'_accessTransition': {'phase': 'ready'}}):
            with self.subTest(change=change):
                self.run.update(change)
                proc = Mock(pid=12345)
                proc.poll.side_effect = lambda: 0 if self.kill.called else None
                self.kill.reset_mock()
                with patch.object(local_spool, 'status_code', return_value=0), \
                        patch.object(local_process, '_HOST_EXIT_GRACE_S', 0):
                    self.assertEqual(self.join(proc), (True, local_process._HOST_EXIT_ERROR))
                self.kill.assert_called_once()
                for key in change:
                    self.run.pop(key)

    def test_old_worker_never_kills_or_settles_a_replacement(self):
        replacement = {**self.run, 'pid': 54321, '_workerIdentity': object()}
        proc = Mock(pid=12345)
        def poll():
            local_process._RUNS[self.ref] = replacement
            return 0
        proc.poll.side_effect = poll
        with patch.object(local_process, '_HOST_EXIT_GRACE_S', 0):
            self.assertEqual(self.join(proc), (False, ''))
        self.kill.assert_not_called()
        self.assertEqual(replacement['state'], 'running')
        self.assertNotIn('_hostCleanup', replacement)

    def test_recovery_requires_a_conclusive_pid_exit_on_every_platform(self):
        for platform in ('win32', 'darwin', 'linux'):
            with self.subTest(platform=platform), \
                    patch.object(local_process.sys, 'platform', platform), \
                    patch('app.services.local_stream._pid_alive', side_effect=[True, False]) as alive:
                self.assertEqual(self.join(), (True, ''))
                self.assertEqual(alive.call_count, 2)
                self.kill.assert_not_called()

    def test_windows_pid_probe_does_not_create_a_console(self):
        from app.services import local_stream
        result = subprocess.CompletedProcess([], 0, stdout=b'"runner.exe","12345"\n')
        with patch.object(local_stream, '_WIN', True), \
                patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                patch.object(subprocess, 'run', return_value=result) as query:
            self.assertTrue(local_stream._pid_alive(12345))
        self.assertEqual(query.call_args.kwargs['creationflags'], 0x08000000)

    def test_targeted_termination_rejects_a_replacement_and_releases_global_lock(self):
        from app.services import local_stream
        proc = Mock()
        self.run['proc'] = proc
        acquired = threading.Event()
        def acquire():
            with local_process._LOCK:
                acquired.set()
        def terminate(pid, observed):
            self.assertEqual(pid, 12345)
            self.assertIs(observed, proc)
            worker = threading.Thread(target=acquire)
            worker.start()
            self.assertTrue(acquired.wait(1), 'Process termination held the global session lock')
            worker.join(timeout=1)
            return True
        with patch.object(local_stream, '_kill_pid_tree', side_effect=terminate) as kill:
            self.assertTrue(local_stream._kill_tree(self.ref, expected_pid=12345, expected_proc=proc))
            self.run['_hostCleanup'] = object()
            self.assertFalse(local_stream._kill_tree(self.ref, expected_pid=12345,
                expected_proc=proc, skip_host_cleanup=True))
            self.run.update(pid=54321, proc=Mock())
            self.assertFalse(local_stream._kill_tree(self.ref, expected_pid=12345, expected_proc=proc))
            kill.assert_called_once()

    def test_watchdog_rechecks_cleanup_ownership_after_poll_before_killing(self):
        for trigger in ('pi', 'timeout'):
            with self.subTest(trigger=trigger):
                self.run.update(cli='pi' if trigger == 'pi' else 'qwen',
                    started=1, lastOut=time.time() if trigger == 'pi' else 1,
                    piFinalResponseAt=time.time() - 60 if trigger == 'pi' else 0)
                proc = Mock()
                self.run['proc'] = proc
                self.run.pop('_hostCleanup', None)
                def poll():
                    self.run['_hostCleanup'] = object()
                    return None
                proc.poll.side_effect = poll
                with patch.object(local_process.threading, 'Thread') as thread:
                    local_process._watchdog(self.ref, proc, finish=Mock(), kill_tree=self.kill)
                target = thread.call_args.kwargs['target']
                with patch.object(local_process.time, 'sleep', side_effect=[None, RuntimeError('stop fixture')]):
                    with self.assertRaisesRegex(RuntimeError, 'stop fixture'):
                        target()
                self.kill.assert_not_called()
                self.assertNotIn('piSettledByWatchdog', self.run)
                self.assertNotIn('timedOut', self.run)

    def test_exit_receipt_protects_host_before_joiner_publishes_its_fence(self):
        self.run.update(cli='pi', started=1, lastOut=1,
            piFinalResponseAt=time.time() - 60, stream={**self.spec, 'version': 1})
        proc = Mock(poll=Mock(return_value=None))
        self.run['proc'] = proc
        with patch.object(local_process.threading, 'Thread') as thread:
            local_process._watchdog(self.ref, proc, finish=Mock(), kill_tree=self.kill)
        target = thread.call_args.kwargs['target']
        with patch.object(local_process.time, 'sleep', side_effect=[None, RuntimeError('stop fixture')]), \
                patch.object(local_spool, 'status_code', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'stop fixture'):
                target()
        self.kill.assert_not_called()
        self.assertNotIn('_hostCleanup', self.run)
        self.assertNotIn('piSettledByWatchdog', self.run)
        self.assertNotIn('timedOut', self.run)

    def test_failed_in_process_policy_restore_cannot_clear_the_pending_host(self):
        proc = Mock(pid=12345)
        self.run.update(proc=proc, _hostCleanup=object(),
            _accessTransition={'phase': 'in-process-applying'})
        old = {'accessPolicy': {'mode': 'standard'}}
        with patch('app.services.local_stream._kill_tree', return_value=False), \
                patch('app.services.access_broker.close'):
            result = access_transition._restore_failed(self.ref, self.run, old)
        self.assertFalse(result['ok'])
        self.assertEqual(self.run['state'], 'running')
        self.assertEqual(self.run['pid'], 12345)
        self.assertIs(self.run['proc'], proc)
        self.assertTrue(self.run['_accessTransition']['cancelled'])
        self.assertEqual(self.run['accessPolicy'], old['accessPolicy'])

    def test_spawn_settles_only_after_the_shared_joiner_confirms_exit(self):
        finished = threading.Event()
        finish = Mock(side_effect=lambda *args: finished.set())
        proc = Mock(pid=12345)
        proc.stdin = Mock()
        with patch.object(local_process, '_env_for', return_value={}), \
                patch.object(local_process, '_command_argv', return_value=['synthetic']), \
                patch('app.services.access_broker.attach'), patch('app.services.access_broker.close'), \
                patch.object(local_process, '_append_raw'), patch.object(local_process, '_watchdog'), \
                patch.object(local_process.subprocess, 'Popen', return_value=proc), \
                patch.object(local_spool, 'prepare', return_value=(self.spec, ['synthetic'])), \
                patch.object(local_spool, 'follow', return_value=(0, 'completed')), \
                patch.object(local_spool, 'cleanup'), \
                patch.object(local_process, '_join_execution_host', return_value=(True, '')) as join:
            local_process.spawn('synthetic', [], 'hello', self.temp.name, self.ref,
                {'accessPolicy': {'mode': 'full'}}, {}, gate_check=lambda *args: '',
                on_line=Mock(), finish=finish, kill_tree=self.kill)
            self.assertTrue(finished.wait(2))
        join.assert_called_once()
        self.assertIs(join.call_args.kwargs['proc'], proc)
        finish.assert_called_once_with(self.ref, 0, {}, '')

    def test_recovery_does_not_finish_or_remove_spool_after_losing_ownership(self):
        finished = Mock()
        joined = threading.Event()
        def join(*args, **kwargs):
            self.assertEqual(kwargs['pid'], 12345)
            joined.set()
            return False, ''
        with patch.object(local_spool, 'follow', return_value=(0, 'completed')), \
                patch.object(local_spool, 'cleanup') as cleanup, \
                patch.object(local_process, '_join_execution_host', side_effect=join):
            before = set(local_process._THREADS)
            local_process.recover(self.ref, on_line=Mock(), finish=finished, kill_tree=self.kill)
            self.assertTrue(joined.wait(2))
            for worker in local_process._THREADS - before:
                worker.join(timeout=2)
            finished.assert_not_called()
            cleanup.assert_not_called()

    def test_recovery_read_failure_still_requires_exit_confirmation(self):
        finished = Mock()
        with patch.object(local_spool, 'follow', side_effect=OSError('synthetic read failure')), \
                patch.object(local_spool, 'cleanup'), \
                patch.object(local_process, '_join_execution_host', return_value=(True, '')) as join:
            before = set(local_process._THREADS)
            local_process.recover(self.ref, on_line=Mock(), finish=finished, kill_tree=self.kill)
            for worker in local_process._THREADS - before:
                worker.join(timeout=2)
            join.assert_called_once_with(self.ref, self.spec, None, self.kill,
                pid=12345, allow_grace=False)
            finished.assert_called_once()
            self.assertEqual(finished.call_args.args[1], 1)

    def test_recovery_tick_cannot_kill_a_replacement_stream(self):
        replacement = {**self.run, 'pid': 54321, 'stream': {'id': 'replacement'},
            'cli': 'pi', 'piFinalResponseAt': time.time() - 60, 'started': 1}
        def follow(*args, **kwargs):
            local_process._RUNS[self.ref] = replacement
            kwargs['on_tick']()
            return 0, 'completed'
        finished = Mock()
        with patch.object(local_spool, 'follow', side_effect=follow), \
                patch.object(local_spool, 'cleanup') as cleanup:
            before = set(local_process._THREADS)
            local_process.recover(self.ref, on_line=Mock(), finish=finished, kill_tree=self.kill)
            for worker in local_process._THREADS - before:
                worker.join(timeout=2)
        self.kill.assert_not_called()
        finished.assert_not_called()
        cleanup.assert_not_called()
        self.assertNotIn('piSettledByWatchdog', replacement)


class ExecutionSettlementTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory(prefix='soduwork-settlement-'))
        self.ref = 'settlement-fixture'
        self.run = {'state': 'running', 'cli': 'pi', 'ws': self.folder, 'cfg': {},
                    'seq': 0, 'lines': [], 'round': 1, 'segment': 1,
                    'conversationMode': 'interactive'}
        self.enterContext(patch.dict(local_base._RUNS, {self.ref: self.run}))
        self.enterContext(patch.object(local_base, '_STORE', self.folder))

    def test_hard_failure_precedes_cancel_timeout_and_semantic_failures(self):
        initial = dict(self.run)
        error = 'Permission-controlled execution host exited abnormally'
        for fields in (
            {'timedOut': 'synthetic timeout'},
            {'stopping': True},
            {'failureKind': 'workspace_permission', 'failurePath': 'fixture'},
            {'failureKind': 'execution_incomplete'},
            {'failureKind': 'execution_host_cleanup'},
        ):
            with self.subTest(fields=fields):
                self.run.clear()
                self.run.update(initial, **fields)
                terminal = {'overflow': True, 'outputTruncated': True}
                with patch.object(local_stream, '_resume') as resume, \
                        patch.object(local_stream, 'authorization_required_sync', return_value=False), \
                        patch.object(local_ctx, '_recover_overflow') as recover:
                    local_stream._finish(self.ref, 1, terminal, error)
                resume.assert_not_called()
                recover.assert_not_called()
                expected_kind = ('execution_host_cleanup'
                                 if fields.get('failureKind') == 'execution_host_cleanup' else None)
                persisted = json.loads(Path(self.folder, self.ref, 'meta.json').read_text(encoding='utf-8'))
                for result in (self.run, persisted):
                    self.assertEqual(result['state'], 'failed')
                    self.assertEqual(result['error'], error)
                    self.assertEqual(result.get('failureKind'), expected_kind)
                    self.assertFalse(result.get('failurePath'))
                self.assertFalse(self.run['stopping'])

    def test_confirmed_cancellation_remains_stopped(self):
        self.run['stopping'] = True
        with patch.object(local_stream, '_resume') as resume:
            local_stream._finish(self.ref, 1, {}, '')
        self.assertEqual(self.run['state'], 'stopped')
        resume.assert_not_called()

    def test_timeout_without_hard_failure_can_resume(self):
        self.run['timedOut'] = 'synthetic timeout'
        with patch.object(local_stream, '_STALL_RETRY_LIMIT', 1), \
                patch.object(local_stream, 'authorization_required_sync', return_value=False), \
                patch.object(local_stream, '_resume', return_value={'ok': True}) as resume:
            local_stream._finish(self.ref, 1, {}, '')
        resume.assert_called_once()
        self.assertEqual(self.run['stallRetries'], 1)
        self.assertEqual(self.run['state'], 'recovering')


if __name__ == '__main__':
    unittest.main()
