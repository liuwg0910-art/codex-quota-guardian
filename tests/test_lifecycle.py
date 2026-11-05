import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
from guardian import lifecycle
class LifecycleTests(unittest.TestCase):
 def setUp(self):
  Path("data").mkdir(exist_ok=True)
 def test_publishes_without_model(self):
  with tempfile.TemporaryDirectory(dir='data') as d, patch.object(lifecycle,'STATE',Path(d)), patch.object(lifecycle,'save_executor_bridge') as save, patch('guardian.autostart.ensure_watcher'):
   self.assertTrue(lifecycle.refresh({'hook_event_name':'SessionStart'})['published']);save.assert_called_once()
 def test_failure_is_visible(self):
  with tempfile.TemporaryDirectory(dir='data') as d, patch.object(lifecycle,'STATE',Path(d)), patch.object(lifecycle,'save_executor_bridge',side_effect=RuntimeError('missing context')):
   self.assertFalse(lifecycle.refresh()['published'])
   self.assertIn('missing context',(Path(d)/'lifecycle-health.json').read_text())

