import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import docsync_wordquest as wq
from docsync_telegram import save


class WordQuestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'root';self.root.mkdir()
        self.state=Path(self.temp.name)/'state';self.state.mkdir()
        self.photo=self.root/'vocabulary.png';self.photo.write_bytes(b'approved image bytes')
        self.index=self.state/wq.REVIEW_FILE;self.calls=[];self.remote_status='wordquest_requested'
        self.record={'id':'a'*32,'sha256':hashlib.sha256(self.photo.read_bytes()).hexdigest(),'status':'wordquest_requested'}
        save(self.index,{self.photo.name:self.record})
        context=patch.object(wq,'work_root',return_value=self.root);context.start();self.addCleanup(context.stop)

    def remote(self,config,payload):
        self.calls.append(payload['op'])
        if payload['op']=='read':return {'status':self.remote_status}
        if payload['op']=='wordquest_extract':return {'words':[['apple','quả táo']]}
        if payload['op']=='wordquest_ready':return {'words':[['apple','quả táo']],'links':['https://example.test/#wq=lesson']}
        if payload['op']=='wordquest_failed':return {'status':'wordquest_failed'}
        raise AssertionError(payload)

    def test_approved_photo_delivers_once_and_keeps_original(self):
        before=self.photo.read_bytes();wq.process(self.state,{},self.remote,save)
        self.assertEqual(before,self.photo.read_bytes())
        self.assertEqual('wordquest_ready',json.loads(self.index.read_text())[self.photo.name]['status'])
        self.assertEqual(1,len(list((self.root/'.docsync/WordQuest').glob('*.json'))))
        wq.process(self.state,{},self.remote,save)
        self.assertEqual(1,self.calls.count('wordquest_extract'))

    def test_changed_image_is_not_uploaded(self):
        self.photo.write_bytes(b'changed photo')
        wq.process(self.state,{},self.remote,save)
        self.assertNotIn('wordquest_extract',self.calls)
        self.assertEqual('wordquest_failed',json.loads(self.index.read_text())[self.photo.name]['status'])
        self.assertEqual(b'changed photo',self.photo.read_bytes())

    def test_server_revocation_or_no_request_never_uploads(self):
        self.remote_status='held';wq.process(self.state,{},self.remote,save)
        self.assertEqual(['read'],self.calls)

    def test_processing_does_not_hold_organizer_lock(self):
        import fcntl
        original=self.remote
        def checked(config,payload):
            if payload['op']=='wordquest_extract':
                with (self.state/'organizer.lock').open('a') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return original(config,payload)
        wq.process(self.state,{},checked,save)


if __name__=='__main__':unittest.main()
