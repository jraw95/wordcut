import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import formats
import media


class Settings(unittest.TestCase):
    def test_reject_invalid_options(self):
        for options in [{'format':'../../escape'},{'mode':'audio','format':'mp4'},
                        {'volume_db':float('nan')},{'volume_db':13},{'fade_in_ms':-1},
                        {'sample_rate':999999},{'include_audio':'true'},{'shell':'oops'}]:
            with self.subTest(options=options),self.assertRaises(ValueError):formats.validate(options)
        with self.assertRaises(ValueError):formats.validate({},audio_only=True)
    def test_filename_is_safe_attachment_name(self):
        name=formats.validate({'name':'../../bad\r\n"name.mp3'})['name']
        self.assertNotIn('/',name);self.assertNotIn('\r',name);self.assertNotIn('"',name)
        self.assertEqual(formats.validate({'name':'test.mp4'})['name'],'test')
    def test_fades_clamped_to_selection(self):
        o=formats.validate({'fade_in_ms':2000,'fade_out_ms':2000})
        f=formats.audio_filter(1,1.1,o)
        self.assertIn('d=0.050000',f);self.assertIn('st=0.050000',f)
    def test_low_rate_mp3_validation(self):
        with self.assertRaises(ValueError):formats.validate({'mode':'audio','format':'mp3','sample_rate':16000,'bitrate':320})
        self.assertEqual(formats.validate({'mode':'audio','format':'mp3','sample_rate':16000,'bitrate':128})['bitrate'],128)
    def test_opus_sample_rates(self):
        with self.assertRaises(ValueError):formats.validate({'mode':'audio','format':'opus','sample_rate':44100})


class Encoders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.folder=Path(cls.tmp.name);cls.cancel=threading.Event()
        media.run(media.ffbase()+['-f','lavfi','-i','color=c=blue:s=160x90:r=25:d=2',
                  '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                  '-c:v','libx264','-threads','2','-c:a','aac',cls.folder/'preview.mp4'],cls.folder,cls.cancel)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_all_ten_output_formats_real_encoding(self):
        for fmt,codec in [('mp4','h264'),('mov','h264'),('mkv','h264'),('webm','vp9'),
                          ('mp3','mp3'),('m4a','aac'),('wav','pcm_s16le'),('flac','flac'),('ogg','vorbis'),('opus','opus')]:
            with self.subTest(format=fmt):
                mode='video' if fmt in formats.VIDEO_FORMATS else 'audio'
                o=formats.validate({'mode':mode,'format':fmt,'channels':'mono','volume_db':-3,
                                    'fade_in_ms':20,'fade_out_ms':30,'name':'test'})
                name='out.'+fmt
                duration=media.export(self.folder,.123,1.123,2,True,name,self.cancel,options=o)
                info=media.probe(self.folder/name)
                self.assertLess(abs(duration-1),.10)
                if mode=='video':self.assertEqual(next(s for s in info['streams'] if s['codec_type']=='video')['codec_name'],codec)
                else:
                    self.assertFalse(any(s['codec_type']=='video' for s in info['streams']))
                    self.assertEqual(info['streams'][0]['codec_name'],codec)
                    self.assertEqual(info['streams'][0]['channels'],1)
    def test_low_sample_rate_vorbis_is_valid(self):
        o=formats.validate({'mode':'audio','format':'ogg','sample_rate':16000,'bitrate':320,'channels':'mono'})
        media.export(self.folder,0,.5,2,False,'speech.ogg',self.cancel,options=o)
        stream=media.probe(self.folder/'speech.ogg')['streams'][0]
        self.assertEqual(stream['sample_rate'],'16000')
    def test_silent_video(self):
        o=formats.validate({'include_audio':False})
        media.export(self.folder,0,1,2,False,'silent.mp4',self.cancel,options=o)
        self.assertEqual([s['codec_type'] for s in media.probe(self.folder/'silent.mp4')['streams']],['video'])
    def test_import_audio_and_preserve_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);source=folder/'upload.wav'
            media.run(media.ffbase()+['-f','lavfi','-i','sine=frequency=550:duration=1',source],folder,self.cancel)
            changes={}
            media.import_file(folder,source,self.cancel,lambda **kw:changes.update(kw))
            self.assertTrue(changes['audio_only']);self.assertEqual(changes['preview'],'preview.m4a')
            self.assertTrue((folder/'master.flac').exists());self.assertTrue((folder/'waveform.bin').exists())
            o=formats.validate({'mode':'audio','format':'flac'},audio_only=True)
            media.export(folder,0,.5,changes['duration'],True,'export.flac',self.cancel,options=o)
            self.assertAlmostEqual(float(media.probe(folder/'export.flac')['format']['duration']),.5,places=3)

if __name__=='__main__':unittest.main()
