from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import signal
import unittest
from unittest.mock import Mock, patch


REPO = Path(__file__).resolve().parents[1]


class OrcaShutdownTests(unittest.TestCase):
    def setUp(self):
        # Load the actual embedded helper without running setup or its main.
        source = (REPO / 'setup.sh').read_text().split('prepare_orca_launch() {\n', 1)[1]
        source = source.split("<<'PY'\n", 1)[1].split('\nPY\n}', 1)[0]
        self.helper = {'__name__': 'orca_shutdown_test'}
        exec(compile(source, 'setup.sh:prepare_orca_launch', 'exec'), self.helper)

    def test_running_desktop_is_closed_automatically_without_a_prompt(self):
        requests = Mock()
        self.helper.update(desktop_instances=Mock(side_effect=[{123: 'start'}, {}]),
                           process_start=Mock(return_value=None), request_exit=requests)
        output = io.StringIO()
        with patch('builtins.open', side_effect=AssertionError('Unexpected restart prompt')):
            with redirect_stdout(output):
                result = self.helper['prepare_launch'](REPO)
        self.assertEqual(result, 0)
        requests.assert_called_once_with(123, 'start')
        self.assertNotIn('Press Enter', output.getvalue())

    def test_no_running_desktop_needs_no_shutdown(self):
        requests = Mock()
        self.helper.update(desktop_instances=Mock(return_value={}), request_exit=requests)
        self.assertEqual(self.helper['prepare_launch'](REPO), 0)
        requests.assert_not_called()

    def test_shutdown_deadline_blocks_profile_changes_if_orca_does_not_exit(self):
        self.helper.update(desktop_instances=Mock(return_value={123: 'start'}),
                           process_start=Mock(return_value='start'), request_exit=Mock())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(self.helper['prepare_launch'](REPO, timeout=0), 1)

    def test_shutdown_sends_sigterm_to_the_original_process(self):
        self.helper['process_start'] = Mock(return_value='start')
        system = self.helper['os']
        signals = self.helper['signal']
        with patch.object(system, 'pidfd_open', return_value=99, create=True), \
                patch.object(signals, 'pidfd_send_signal', create=True) as send, \
                patch.object(system, 'close') as close:
            self.helper['request_exit'](123, 'start')
        send.assert_called_once_with(99, signal.SIGTERM)
        close.assert_called_once_with(99)

    def test_reused_pid_is_not_signaled(self):
        self.helper['process_start'] = Mock(return_value='different-start')
        system = self.helper['os']
        signals = self.helper['signal']
        with patch.object(system, 'pidfd_open', return_value=99, create=True), \
                patch.object(signals, 'pidfd_send_signal', create=True) as send, \
                patch.object(system, 'close'):
            self.helper['request_exit'](123, 'start')
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
