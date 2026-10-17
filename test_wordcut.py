import array
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import media
import server


class Validation(unittest.TestCase):
    def test_time_formats_and_milliseconds(self):
        self.assertEqual(media.parse_time('1:02.345'),62.345)
        self.assertEqual(media.parse_time('1:02:03.004'),3723.004)
        for value in ['-1','nan','1:60','1:99:00','1.1234',True,'1;touch file']:
            with self.assertRaises(ValueError):media.parse_time(value)
    def test_padding_clamps_both_video_boundaries(self):
        self.assertEqual(media.selection('0:00','0:28',2884),(0,28,0,29))
        self.assertEqual(media.selection('12.500','20',30),(12.5,20,11.5,21))
        self.assertEqual(media.selection(28,30,30),(28,30,27,30))
    def test_invalid_selection(self):
        for start,end,duration in [(2,1,10),(0,601,1000),(9,11,10),(10,11,10)]:
            with self.assertRaises(ValueError):media.selection(start,end,duration)
    def test_url_allowlist_and_canonicalization(self):
        result='https://www.youtube.com/watch?v=abcDEF12345'
        self.assertEqual(media.youtube_url('https://youtu.be/abcDEF12345?t=123'),result)
        self.assertEqual(media.youtube_url('https://m.youtube.com/shorts/abcDEF12345'),result)
        for url in ['file:///etc/passwd','http://127.0.0.1/','https://youtube.com.evil.test/watch?v=abcDEF12345','https://youtube.com@evil.test/watch?v=abcDEF12345','https://youtube.com:8190/watch?v=abcDEF12345']:
            with self.assertRaises(ValueError):media.youtube_url(url)
    def test_cut_bounds(self):
        self.assertEqual(media.cut_bounds(.001,1.234,2),(.001,1.234))
        for a,b in [(float('nan'),1),(0,float('inf')),(-1,2),(2,1),(0,3),(1,1.01)]:
            with self.assertRaises(ValueError):media.cut_bounds(a,b,2)


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.key='a'*32;(cls.root/cls.key).mkdir();(cls.root/cls.key/'preview.mp4').write_bytes(b'0123456789')
        cls.patch=patch.object(server,'JOBS',cls.root);cls.patch.start()
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.http.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join();cls.patch.stop();cls.temp.cleanup()
    def request(self,path,headers=None,data=None,method=None):
        h={'Host':'127.0.0.1:18447'};h.update(headers or {})
        req=urllib.request.Request(self.url+path,headers=h,data=data,method=method)
        try:
            with urllib.request.urlopen(req,timeout=3) as r:return r.status,dict(r.headers),r.read()
        except urllib.error.HTTPError as r:return r.code,dict(r.headers),r.read()
    def test_safari_range_and_suffix(self):
        path='/media/'+self.key+'/preview.mp4'
        code,headers,body=self.request(path,{'Range':'bytes=0-1'})
        self.assertEqual((code,body),(206,b'01'));self.assertEqual(headers['Content-Range'],'bytes 0-1/10')
        self.assertEqual(self.request(path,{'Range':'bytes=-3'})[2],b'789')
        self.assertEqual(self.request(path,{'Range':'bytes=100-200'})[0],416)
        self.assertEqual(self.request(path,method='HEAD')[2],b'')
    def test_cross_origin_and_host_rejected(self):
        headers={'Content-Type':'application/json','X-Wordcut':'1','Origin':'https://evil.test'}
        self.assertEqual(self.request('/api/prepare',headers,b'{}')[0],403)
        self.assertEqual(self.request('/healthz',{'Host':'evil.test'})[0],403)
    def test_media_traversal_is_not_served(self):
        self.assertEqual(self.request('/media/'+self.key+'/../../server.py')[0],404)
    def test_job_concurrency_rejected(self):
        headers={'Content-Type':'application/json','X-Wordcut':'1','Origin':'http://127.0.0.1:18447'}
        with patch.object(server,'ACTIVE','occupied'):
            self.assertEqual(self.request('/api/prepare',headers,b'{}')[0],400)


class RealMedia(unittest.TestCase):
    def test_audio_cut_crosses_expected_tone_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); cancel=threading.Event()
            # Known frequency change at 2 s: exported midpoint must retain that change.
            media.run(media.ffbase()+['-f','lavfi','-i','color=c=blue:s=160x90:r=25:d=4',
                '-f','lavfi','-i',r'aevalsrc=if(lt(t\,2)\,sin(2*PI*440*t)\,sin(2*PI*880*t)):s=48000:d=4',
                '-c:v','libx264','-threads','2','-preset','ultrafast','-c:a','aac',folder/'preview.mp4'],folder,cancel)
            result=media.export(folder,1.501,2.501,4,False,'clip-test.mp4',cancel)
            self.assertLess(abs(result-1),.05)
            media.run(media.ffbase()+['-i',folder/'clip-test.mp4','-vn','-ac','1','-ar','48000',folder/'decoded.wav'],folder,cancel)
            with wave.open(str(folder/'decoded.wav')) as wav:
                samples=array.array('h');samples.frombytes(wav.readframes(wav.getnframes()))
                self.assertLess(abs(wav.getnframes()/48000-1),.023)
            for start,end,expected in [(.1,.4,440),(.6,.9,880)]:
                chunk=samples[round(start*48000):round(end*48000)]
                crossings=sum(a<=0<b for a,b in zip(chunk,chunk[1:]))/(end-start)
                self.assertLess(abs(crossings-expected),8)
    def test_cancel_terminates_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            cancel=threading.Event();cancel.set()
            with self.assertRaises(media.Cancelled):
                media.run(['/usr/bin/python3','-c','import time;time.sleep(30)'],Path(tmp),cancel,timeout=2)
    def test_cleanup_tolerates_manually_removed_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,kept='c'*32,'d'*32;(root/kept).mkdir();(root/kept/'job.json').write_text('{}')
            jobs={old:{'id':old,'created':0},kept:{'id':kept,'created':9e9}}
            with patch.object(server,'JOBS',root),patch.object(server,'RECORDS',jobs),patch.object(server,'ACTIVE',None),\
                 patch.object(server.shutil,'disk_usage',return_value=SimpleNamespace(free=10*2**30)):
                server.cleanup()
            self.assertEqual(list(jobs),[kept])

if __name__=='__main__':unittest.main()
