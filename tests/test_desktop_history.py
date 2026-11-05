import unittest
from guardian.desktop import Desktop
from guardian.core import read_task
class DesktopHistoryTests(unittest.TestCase):
 def test_desktop_quota_error_without_code(self):
  from guardian.core import quota_failure
  self.assertTrue(quota_failure({'status':'failed','error':{'message':'You\u2019ve hit your usage limit. Upgrade to Pro'}}))
  self.assertFalse(quota_failure({'status':'completed','error':{'message':"You've hit your usage limit."}}))
  self.assertFalse(quota_failure({'status':'failed','error':{'message':'Network error'}}))
  self.assertFalse(quota_failure({'status':'failed','error':{'codexErrorInfo':'other','message':"You've hit your usage limit."}}))
 def test_newest_first_history_is_normalized(self):
  bridge=Desktop.__new__(Desktop)
  bridge.tool=lambda *a,**k: {'thread':{'id':'x','status':{'type':'idle'}},'page':{'order':'newest_first'},'turns':[{'id':'new','status':'failed','error':{'codexErrorInfo':'usageLimitExceeded'}},{'id':'old','status':'completed'}]}
  thread=read_task(None,'x',bridge)
  self.assertEqual([t['id'] for t in thread['turns']],['old','new'])
  self.assertEqual(thread['turns'][-1]['error']['codexErrorInfo'],'usageLimitExceeded')
 def test_read_failure_does_not_fall_back_to_stale_history(self):
  bridge=Desktop.__new__(Desktop)
  def fail(*a,**k): raise RuntimeError('offline')
  bridge.tool=fail
  with self.assertRaises(RuntimeError): read_task(None,'x',bridge)
