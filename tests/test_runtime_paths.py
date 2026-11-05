import unittest
from unittest.mock import patch
from pathlib import Path
from guardian.desktop import runtime_paths
class RuntimePathsTests(unittest.TestCase):
 def test_upgrade_repairs_runtime_without_changing_endpoint(self):
  config={'server':'missing-server','node':'missing-node','pipe':'actual-endpoint','callerThread':'actual-caller'}
  with patch.object(Path,'is_file',return_value=False), patch.object(Path,'glob',return_value=iter([Path('plugin/0.2.0/server.mjs')])), patch('guardian.desktop.shutil.which',return_value='new-node'):
   result=runtime_paths(config)
  self.assertEqual(result['node'],'new-node')
  self.assertEqual(result['pipe'],config['pipe'])
  self.assertEqual(result['callerThread'],config['callerThread'])
  self.assertEqual(config['node'],'missing-node')
 def test_existing_paths_are_preserved(self):
  config={'server':'server','node':'node'}
  with patch.object(Path,'is_file',return_value=True): self.assertEqual(runtime_paths(config),config)
