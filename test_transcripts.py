import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import media
import server
import transcripts
import test_wordcut


class CaptionParsing(unittest.TestCase):
    def test_rollup_overlap_removed_but_repeated_speech_retained(self):
        data={'events':[
            {'tStartMs':0,'dDurationMs':2000,'segs':[{'utf8':'Hello &amp; '},{'utf8':'welcome\n'}]},
            {'tStartMs':1000,'dDurationMs':2000,'segs':[{'utf8':'Hello & welcome'}]},
            {'tStartMs':3000,'dDurationMs':1000,'segs':[{'utf8':'Hello & welcome'}]},
            {'tStartMs':4000,'dDurationMs':1000,'segs':[{'utf8':'<script>literal text</script>'}]},
            {'tStartMs':5000,'dDurationMs':1000,'segs':[{'utf8':'\n'}]}]}
        result=transcripts.parse_json3(data)
        self.assertEqual(len(result),3)
        self.assertEqual(result[0],{'start':0,'end':3,'text':'Hello & welcome'})
        self.assertEqual(result[-1]['text'],'<script>literal text</script>')
    def test_invalid_or_empty_timing_is_not_displayed(self):
        for start,duration in [(float('nan'),1000),(0,float('inf')),(-1,1000),(0,0)]:
            with self.assertRaises(ValueError):
                transcripts.parse_json3({'events':[{'tStartMs':start,'dDurationMs':duration,'segs':[{'utf8':'x'}]}]})
    def test_source_language_and_creator_tracks_preferred(self):
        entry=[{'ext':'json3','name':'German','url':'private URL'}]
        info={'language':'de','subtitles':{'de':entry,'live_chat': [{'ext':'json'}]},
              'automatic_captions':{'en':entry,'de':entry,'../bad':entry}}
        tracks=transcripts.tracks(info)
        self.assertEqual(tracks[0]['id'],'manual:de')
        self.assertEqual(len(tracks),3)
        self.assertNotIn('private URL',json.dumps(tracks))


class Extraction(unittest.TestCase):
    def test_caption_only_download_and_metadata_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); calls=[]
            def run(args,work,cancel,**kwargs):
                calls.append(args)
                if '--dump-single-json' in args:
                    kwargs['stdout'].write_text(json.dumps({'language':'en','automatic_captions':{'en':[{'ext':'json3'}]}}))
                else:
                    (folder/'captions.en.json3').write_text(json.dumps({'events':[{'tStartMs':0,'dDurationMs':1000,'segs':[{'utf8':'Test caption.'}]}]}))
            with patch.object(media,'run',side_effect=run):
                result=transcripts.extract(folder,'https://youtu.be/abcDEF12345','',threading.Event())
            self.assertEqual(result['cues'][0]['text'],'Test caption.')
            self.assertTrue(all('--skip-download' in call for call in calls))
            self.assertIn('--write-auto-subs',calls[1])
            self.assertEqual([p.name for p in folder.iterdir()],['transcript.json'])
    def test_no_captions_and_invalid_language_keep_previous_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'transcript.json').write_text('previous')
            def run(*args,**kwargs):kwargs['stdout'].write_text('{}')
            with patch.object(media,'run',side_effect=run),self.assertRaisesRegex(ValueError,'no downloadable captions'):
                transcripts.extract(folder,'https://youtu.be/abcDEF12345','',threading.Event())
            self.assertEqual((folder/'transcript.json').read_text(),'previous')
            self.assertFalse((folder/'caption-info.json').exists())
    def test_failure_without_process_log_keeps_curated_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(media,'run',side_effect=RuntimeError('raw')),self.assertRaisesRegex(RuntimeError,'Could not download captions'):
                transcripts.extract(Path(tmp),'https://youtu.be/abcDEF12345','',threading.Event())
    def test_worker_failure_or_cancel_does_not_break_ready_clip(self):
        for error in [media.Cancelled(), RuntimeError('Caption download unavailable.')]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);key='b'*32;(root/key).mkdir()
                jobs={key:{'id':key,'state':'ready','export':'clip-existing.mp4','transcript_state':'loading'}}
                with patch.object(server,'JOBS',root),patch.object(server,'RECORDS',jobs),patch.object(server,'ACTIVE',key),patch.object(transcripts,'extract',side_effect=error):
                    server.work('transcript',key,('https://youtu.be/abcDEF12345',''))
                    self.assertIsNone(server.ACTIVE)
                    self.assertEqual(jobs[key]['state'],'ready')
                    self.assertEqual(jobs[key]['export'],'clip-existing.mp4')
                    self.assertEqual(jobs[key]['transcript_state'],'error')


class TranscriptHttp(unittest.TestCase):
    setUpClass = classmethod(test_wordcut.Http.setUpClass.__func__)
    tearDownClass = classmethod(test_wordcut.Http.tearDownClass.__func__)
    request = test_wordcut.Http.request

    def test_extract_validation_and_concurrency(self):
        headers={'Content-Type':'application/json','X-Wordcut':'1','Origin':'http://127.0.0.1:18447'}
        record={'id':self.key,'state':'ready','url':'https://youtu.be/abcDEF12345'}
        with patch.object(server,'RECORDS',{self.key:record}),patch.object(server,'submit') as submit:
            code,_,_=self.request('/api/transcript',headers,json.dumps({'id':self.key,'language':'../bad'}).encode())
            self.assertEqual(code,400);submit.assert_not_called()
            with patch.object(server,'ACTIVE','occupied'):
                self.assertEqual(self.request('/api/transcript',headers,json.dumps({'id':self.key}).encode())[0],400)
            record['url']=''
            self.assertEqual(self.request('/api/transcript',headers,json.dumps({'id':self.key}).encode())[0],400)
            record['url']='https://youtu.be/abcDEF12345'
            self.assertEqual(self.request('/api/transcript',headers,json.dumps({'id':self.key}).encode())[0],202)
            submit.assert_called_once_with('transcript',self.key,(record['url'],''))
    def test_saved_transcript_and_origin_protection(self):
        data={'cues':[{'start':0,'end':1,'text':'Safe text'}]}
        (self.root/self.key/'transcript.json').write_text(json.dumps(data))
        with patch.object(server,'RECORDS',{self.key:{'id':self.key}}):
            code,_,body=self.request('/api/jobs/'+self.key+'/transcript')
            self.assertEqual(code,200);self.assertEqual(json.loads(body),data)
        self.assertEqual(self.request('/api/transcript',{'Origin':'http://evil.test'},b'{}')[0],403)
        self.assertEqual(self.request('/api/jobs/../../etc/passwd/transcript')[0],404)
