#!/usr/bin/python3
"""Loopback/LAN-only Wordcut server. One bounded media job at a time, no GPU use."""
import concurrent.futures
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote
import uuid
import media
import formats
import transcripts

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('WORDCUT_STATE', str(Path.home()/'.local/state/wordcut')))
JOBS = STATE/'jobs'
PORT = int(os.environ.get('WORDCUT_PORT', '18447'))
# Loopback only unless both LAN settings are given; never binds all interfaces.
LAN = os.environ.get('WORDCUT_LAN_IP') or None
NETWORK = ipaddress.ip_network(os.environ['WORDCUT_LAN_NETWORK']) if LAN and os.environ.get('WORDCUT_LAN_NETWORK') else None
if LAN and not NETWORK: raise SystemExit('Set WORDCUT_LAN_NETWORK (for example 192.168.1.0/24) together with WORDCUT_LAN_IP.')
HOSTS = {f'{host}:{PORT}' for host in ('127.0.0.1', 'localhost', *([LAN, socket.gethostname(), socket.gethostname()+'.local'] if LAN else []))}
UPLOAD_SECONDS = 15*60
LOCK = threading.RLock()
WORKER = concurrent.futures.ThreadPoolExecutor(max_workers=1)
ACTIVE = None
CANCEL = threading.Event()
RECORDS = {}


def initialize():
    JOBS.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in JOBS.glob('*/job.json'):
        try:
            job = json.loads(path.read_text())
            if job['state'] in ('uploading', 'preparing', 'exporting'):
                job.update(state='ready' if (path.parent/'waveform.bin').exists() else 'error',
                           error='The service restarted during processing. Please retry.', phase='Interrupted')
            if job.get('transcript_state') == 'loading':
                job.update(transcript_state='error', transcript_error='Transcript extraction was interrupted. Please retry.')
            RECORDS[job['id']] = job
        except (OSError, ValueError, KeyError): pass


def save(job):
    path = JOBS/job['id']/'job.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(job)); temp.replace(path)


def update(key, **values):
    with LOCK:
        RECORDS[key].update(values, updated=time.time())
        save(RECORDS[key])


def get(key):
    with LOCK:
        if key not in RECORDS: raise ValueError('Clip not found.')
        return dict(RECORDS[key])


def size(paths):
    total = 0
    for path in paths:
        try: total += path.stat().st_size if path.is_file() else 0
        except OSError: pass  # Removed meanwhile.
    return total


def cleanup():
    """Only expired app-owned jobs, never arbitrary files; keep cache below 10 GiB."""
    total = size(JOBS.glob('*/*'))
    for job in sorted(list(RECORDS.values()), key=lambda j:j['created']):
        if total < 10*2**30 and time.time()-job['created'] < 7*86400: continue
        if job['id'] == ACTIVE: continue
        folder = JOBS/job['id']
        total -= size(folder.glob('*'))
        shutil.rmtree(folder, ignore_errors=True); RECORDS.pop(job['id'], None)
    if shutil.disk_usage(JOBS).free < 5*2**30:
        raise ValueError('Less than 5 GiB of disk space remains. Free some space before downloading.')


def submit(action, key, args):
    global ACTIVE
    with LOCK:
        if ACTIVE: raise ValueError('Another clip is processing. Wait or cancel it first.')
        CANCEL.clear(); ACTIVE = key
        WORKER.submit(work, action, key, args)


def work(action, key, args):
    global ACTIVE
    try:
        if action == 'prepare':
            media.prepare(JOBS/key, *args, CANCEL, lambda **kw:update(key, **kw))
        elif action == 'import':
            media.import_file(JOBS/key, *args, CANCEL, lambda **kw:update(key, **kw))
        elif action == 'transcript':
            result = transcripts.extract(JOBS/key, *args, CANCEL)
            update(key, transcript_state='ready', transcript_error=None, transcript_revision=result['revision'])
        else:
            job = get(key)
            options=job['export_options']
            filename = 'clip-'+uuid.uuid4().hex[:12]+'.'+options['format']
            duration = media.export(JOBS/key, *args, job['duration'], job['fade'], filename, CANCEL, options=options)
            update(key, state='ready', phase='Export ready', progress=100,
                   export=filename, export_duration=duration, export_bytes=(JOBS/key/filename).stat().st_size,
                   download_name=options['name']+'.'+options['format'], error=None)
            for path in (JOBS/key).glob('clip-*.*'):
                if path.name != filename: path.unlink()
    except media.Cancelled:
        if action == 'transcript':
            update(key, transcript_state='error', transcript_error='Transcript extraction cancelled.')
        else:
            update(key, state='ready' if action == 'export' else 'error', phase='Cancelled', error='Cancelled.')
    except Exception as exc:
        # Only curated media errors leave the server; no subprocess logs/URLs.
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else 'Processing failed. Please retry.'
        if action == 'transcript':
            update(key, transcript_state='error', transcript_error=message)
        else:
            update(key, state='ready' if action == 'export' else 'error', phase='Could not finish', error=message)
    finally:
        with LOCK: ACTIVE = None


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup(); self.connection.settimeout(20)

    def log_message(self, *args): pass

    def allowed(self):
        try:
            address = ipaddress.ip_address(self.client_address[0])
            return (address.is_loopback or bool(NETWORK and address in NETWORK)) and self.headers.get('Host') in HOSTS
        except ValueError: return False

    def headers_common(self, code, mime, length, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        for key,value in (extra or {}).items(): self.send_header(key,value)
        self.end_headers()

    def reply(self, code, data):
        body=json.dumps(data).encode(); self.headers_common(code,'application/json',len(body))
        if self.command != 'HEAD': self.wfile.write(body)

    def file(self, path, download=False, download_name=None):
        if not path.is_file(): return self.reply(404,{'error':'File not found.'})
        size=path.stat().st_size; start=0; end=size-1; code=200
        headers={'Accept-Ranges':'bytes'}
        mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        if download: headers['Content-Disposition']='attachment; filename="'+(download_name or 'wordcut-'+path.name)+'"'
        if value:=self.headers.get('Range'):
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
            try:
                if not match or not any(match.groups()): raise ValueError()
                a,b=match.groups()
                if a:
                    start=int(a); end=min(int(b),end) if b else end
                else: start=max(0,size-int(b))
                if start>end or start>=size: raise ValueError()
            except ValueError:
                self.headers_common(416,mime,0,{'Content-Range':f'bytes */{size}'})
                return
            code=206;headers['Content-Range']=f'bytes {start}-{end}/{size}'
        self.headers_common(code,mime,end-start+1,headers)
        if self.command=='HEAD': return
        try:
            with path.open('rb') as source:
                source.seek(start); remaining=end-start+1
                while remaining:
                    chunk=source.read(min(256*1024,remaining))
                    if not chunk: break
                    self.wfile.write(chunk);remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError,TimeoutError): pass

    def do_HEAD(self): self.do_GET()

    def do_GET(self):
        if not self.allowed(): return self.reply(403,{'error':'Available on the local network only.'})
        path=urlsplit(self.path).path
        if path=='/healthz': return self.reply(200,{'ok':True})
        if path=='/api/jobs':
            with LOCK:
                return self.reply(200,{'jobs':sorted(RECORDS.values(),key=lambda j:j['created'],reverse=True)[:30], 'active':ACTIVE})
        match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})/transcript',path)
        if match:
            try:
                get(match[1])
                data=json.loads((JOBS/match[1]/'transcript.json').read_text())
                return self.reply(200,data)
            except (ValueError,OSError):
                return self.reply(404,{'error':'Extract a transcript first.'})
        match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})',path)
        if match:
            try:return self.reply(200,get(match[1]))
            except ValueError:return self.reply(404,{'error':'Clip not found.'})
        match=re.fullmatch(r'/media/([a-f0-9]{32})/(preview\.(?:mp4|m4a)|audio\.wav|waveform\.bin|clip-[a-f0-9]{12}\.(?:mp4|mov|mkv|webm|mp3|m4a|wav|flac|ogg|opus))',path)
        if match:
            with LOCK:
                job=RECORDS.get(match[1],{})
                name=job.get('download_name') if job.get('export')==match[2] else None
            return self.file(JOBS/match[1]/match[2],download=match[2].startswith('clip-'),download_name=name)
        files={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if path in files:return self.file(ROOT/'static'/files[path])
        self.reply(404,{'error':'Not found.'})

    def upload(self):
        global ACTIVE
        key=None;handed_off=False
        try:
            if self.headers.get('Content-Type')!='application/octet-stream':raise ValueError('Invalid upload type.')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=250*1024**2:raise ValueError('Upload a file up to 250 MiB.')
            name=Path(unquote(self.headers.get('X-Filename','upload'))).name[:180]
            suffix=Path(name).suffix.lower()
            if suffix not in {'.mp3','.m4a','.wav','.flac','.ogg','.opus','.aac','.aiff','.aif','.mp4','.mov','.mkv','.webm'}:
                raise ValueError('Choose a supported audio or video file.')
            with LOCK:
                if ACTIVE:raise ValueError('Another clip is processing. Wait or cancel it first.')
                cleanup();key=uuid.uuid4().hex;(JOBS/key).mkdir(mode=0o700)
                RECORDS[key]={'id':key,'title':name,'url':'','created':time.time(),'updated':time.time(),
                              'state':'uploading','phase':'Uploading file','progress':0,'requested_start':0,
                              'requested_end':0,'error':None,'source_kind':'upload'}
                save(RECORDS[key]);ACTIVE=key;CANCEL.clear()
            target=JOBS/key/('upload'+suffix)
            deadline=time.monotonic()+UPLOAD_SECONDS
            with target.open('wb') as out:
                remaining=length
                while remaining:
                    if CANCEL.is_set():raise media.Cancelled()
                    if time.monotonic()>deadline:raise ValueError('Upload took too long. Try again on a faster connection.')
                    block=self.rfile.read(min(1024**2,remaining))
                    if not block:raise ValueError('Upload interrupted.')
                    out.write(block);remaining-=len(block)
            with LOCK:
                update(key,state='preparing',phase='Reading uploaded file')
                ACTIVE=None;submit('import',key,(target,));handed_off=True
            return self.reply(202,get(key))
        except Exception as exc:
            if handed_off:return  # Client disconnected after the worker took ownership.
            if key:
                with LOCK:
                    update(key,state='error',phase='Upload failed',error='Upload interrupted or cancelled.')
                    ACTIVE=None
                for file in (JOBS/key).glob('upload.*'):file.unlink(missing_ok=True)
            self.close_connection=True
            return self.reply(400,{'error':str(exc) if isinstance(exc,ValueError) else 'Upload interrupted or cancelled.'})

    def do_POST(self):
        if (not self.allowed() or self.headers.get('Origin')!='http://'+self.headers.get('Host','')
            or self.headers.get('X-Wordcut')!='1'):
            return self.reply(403,{'error':'Open Wordcut directly on your local network to use its controls.'})
        if self.path=='/api/upload':return self.upload()
        if self.headers.get('Content-Type')!='application/json':return self.reply(415,{'error':'Expected JSON.'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:raise ValueError('Invalid request.')
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise ValueError('Invalid request.')
            with LOCK:
                if self.path=='/api/prepare':
                    if ACTIVE:raise ValueError('Another clip is processing. Wait or cancel it first.')
                    url=media.youtube_url(data.get('url',''))
                    start,end=media.parse_time(data.get('start','')),media.parse_time(data.get('end',''))
                    if not start<end or end-start>media.MAX_SECONDS:raise ValueError('Choose a selection longer than zero and no longer than 10 minutes.')
                    cleanup();key=uuid.uuid4().hex
                    (JOBS/key).mkdir(mode=0o700)
                    job={'id':key,'title':'Reading YouTube video…','url':url,'created':time.time(),
                         'updated':time.time(),'state':'preparing','phase':'Starting','progress':0,
                         'requested_start':start,'requested_end':end,'error':None}
                    RECORDS[key]=job;save(job);submit('prepare',key,(url,start,end))
                    return self.reply(202,dict(job))
                if self.path=='/api/transcript':
                    key=data.get('id');job=get(key)
                    if ACTIVE:raise ValueError('Another clip is processing. Wait or cancel it first.')
                    if job['state']!='ready':raise ValueError('Wait until the preview is ready.')
                    if not job.get('url'):raise ValueError('Transcript extraction currently uses YouTube captions. Uploaded files do not have a YouTube caption track.')
                    language=data.get('language','')
                    if not isinstance(language,str) or (language and not re.fullmatch(r'(auto|manual):[A-Za-z0-9_-]{1,40}',language)):
                        raise ValueError('Choose a caption language from the list.')
                    update(key,transcript_state='loading',transcript_error=None)
                    submit('transcript',key,(job['url'],language))
                    return self.reply(202,get(key))
                if self.path=='/api/export':
                    key=data.get('id');job=get(key)
                    if ACTIVE:raise ValueError('Another clip is processing. Wait or cancel it first.')
                    if job['state']!='ready':raise ValueError('Wait until the preview is ready.')
                    start,end=media.cut_bounds(data.get('start'),data.get('end'),job['duration'])
                    options=formats.validate(data.get('options') or {'fade_in_ms':3 if data.get('fade') else 0, 'fade_out_ms':3 if data.get('fade') else 0},audio_only=job.get('audio_only',False))
                    if shutil.disk_usage(JOBS).free<2*1024**3:raise ValueError('Free at least 2 GiB before exporting.')
                    update(key,state='exporting',phase='Creating your precise cut',progress=30,
                           cut_start=start,cut_end=end,fade=bool(options['fade_in_ms'] or options['fade_out_ms']),export_options=options,export=None,error=None)
                    submit('export',key,(start,end));return self.reply(202,get(key))
                if self.path=='/api/cancel':
                    if data.get('id')!=ACTIVE or ACTIVE is None:raise ValueError('This clip is not processing.')
                    CANCEL.set();return self.reply(200,{'ok':True})
                return self.reply(404,{'error':'Not found.'})
        except (ValueError,TypeError,KeyError):
            # ValueError messages are curated validation messages, not raw logs.
            import sys
            exc=sys.exc_info()[1]
            self.reply(400,{'error':str(exc) if isinstance(exc,ValueError) else 'Invalid request.'})
        except Exception:
            self.reply(500,{'error':'Unable to process the request. Please try again.'})


def main():
    os.umask(0o077); initialize()
    servers=[]
    for address in ['127.0.0.1',*([LAN] if LAN else [])]:
        http=ThreadingHTTPServer((address,PORT),Handler)
        servers.append(http)
        threading.Thread(target=http.serve_forever,daemon=True).start()
    print('Wordcut ready on loopback'+(' and the configured LAN interface.' if LAN else ' only.'),flush=True)
    threading.Event().wait()

if __name__=='__main__':main()
