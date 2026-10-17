"""Bounded, CPU-only YouTube segment preparation and frame/audio-accurate export."""
import array
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.parse
import wave
import formats

ROOT = Path(__file__).resolve().parent
YTDLP = ROOT/'.venv/bin/yt-dlp'
NODE = os.environ.get('WORDCUT_NODE', str(Path.home()/'.local/bin/node'))
FFMPEG = os.environ.get('WORDCUT_FFMPEG', '/usr/bin/ffmpeg')
FFPROBE = os.environ.get('WORDCUT_FFPROBE', '/usr/bin/ffprobe')
MAX_SECONDS = 600
SAFE_DEMUX = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,wav,mp3,flac,ogg,aac,aiff"

class Cancelled(Exception): pass


def parse_time(value):
    if isinstance(value, bool):
        raise ValueError('Enter a valid time.')
    text = str(value).strip()
    if not re.fullmatch(r'\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?', text):
        raise ValueError('Use seconds, M:SS or H:MM:SS, optionally with .milliseconds.')
    parts = [float(x) for x in text.split(':')]
    if any(x >= 60 for x in parts[1:]):
        raise ValueError('Minutes and seconds after a colon must be below 60.')
    result = 0
    for part in parts:
        result = result*60+part
    if not math.isfinite(result) or result > 604800:
        raise ValueError('Time is out of range.')
    return result


def youtube_url(text):
    try:
        u = urllib.parse.urlsplit(text.strip())
        if u.scheme not in ('https', 'http') or u.username or u.password or u.port not in (None, 80, 443):
            raise ValueError()
        host = (u.hostname or '').lower()
        if host in ('youtu.be', 'www.youtu.be'):
            video = u.path.strip('/')
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
            if u.path == '/watch':
                video = urllib.parse.parse_qs(u.query).get('v', [''])[0]
            elif u.path.startswith(('/shorts/', '/embed/', '/live/')):
                video = u.path.split('/')[2]
            else:
                raise ValueError()
        else:
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
            raise ValueError()
    except (ValueError, AttributeError, IndexError):
        raise ValueError('Paste a single YouTube video link.') from None
    return 'https://www.youtube.com/watch?v=' + video


def selection(start, end, duration):
    start, end = parse_time(start), parse_time(end)
    if not start < end:
        raise ValueError('End must be later than start.')
    if end-start > MAX_SECONDS:
        raise ValueError('Select up to 10 minutes at a time.')
    if start >= duration or end > duration+.05:
        raise ValueError('The selected times extend beyond this video.')
    end = min(end, duration)
    return start, end, max(0, start-1), min(duration, end+1)


def run(args, folder, cancel, timeout=900, stdout=None):
    """No shell. One subprocess group, bounded wall time and output file size."""
    log = folder/'process.log'
    with log.open('ab') as err:
        with (stdout.open('wb') if stdout else open(os.devnull, 'wb')) as out:
            process = subprocess.Popen([str(x) for x in args], stdout=out, stderr=err, start_new_session=True)
            deadline = time.monotonic()+timeout
            try:
                while process.poll() is None:
                    if cancel.wait(.2):
                        raise Cancelled('Cancelled.')
                    if time.monotonic() > deadline:
                        raise RuntimeError('The operation timed out. Try a shorter selection.')
                if process.returncode:
                    tail = log.read_bytes()[-5000:].decode(errors='replace')
                    if 'Sign in' in tail or 'confirm you' in tail:
                        raise RuntimeError('YouTube requires sign-in for this request. Try another public video.')
                    if 'HTTP Error 403' in tail or '403 Forbidden' in tail:
                        raise RuntimeError('YouTube refused the video download (403). The downloader may need updating.')
                    raise RuntimeError('Video processing failed. Try again or choose another public video.')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL); process.wait()


def ffbase():
    return [FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-threads', '2', '-filter_threads', '1']


def ytdlp_base():
    return [YTDLP, '--ignore-config', '--no-playlist', '--no-cache-dir', '--js-runtimes', f'node:{NODE}',
            '--socket-timeout', '20', '--retries', '2', '--fragment-retries', '2', '--no-progress', '--no-warnings']


def probe(path):
    result = subprocess.run([FFPROBE, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-format_whitelist', SAFE_DEMUX, '-show_format', '-show_streams', '-of', 'json', str(path)],
                            capture_output=True, text=True, timeout=20, check=True)
    return json.loads(result.stdout)


def normalize(folder, source, duration, cancel):
    info = probe(source)
    audio = any(s['codec_type'] == 'audio' for s in info['streams'])
    if not audio:
        raise ValueError('This video has no audio track to display or cut between words.')
    has_video = any(s['codec_type'] == 'video' for s in info['streams'])
    # Preserve decoded source audio for conversion without a further lossy generation.
    run(ffbase()+['-protocol_whitelist', 'file,pipe', '-format_whitelist', SAFE_DEMUX, '-i', source, '-t', f'{duration:.6f}', '-vn', '-map', '0:a:0',
        '-c:a', 'flac', '-sample_fmt', 's32', folder/'master.flac'], folder, cancel)
    if has_video:
        run(ffbase()+['-protocol_whitelist', 'file,pipe', '-format_whitelist', SAFE_DEMUX, '-i', source, '-t', f'{duration:.6f}', '-map', '0:v:0', '-map', '0:a:0',
            '-vf', "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
            '-c:v', 'libx264', '-threads', '2', '-preset', 'fast', '-crf', '19', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', folder/'preview.mp4'], folder, cancel)
    else:
        run(ffbase()+['-i', folder/'master.flac', '-c:a', 'aac', '-b:a', '192k',
                     '-movflags', '+faststart', folder/'preview.m4a'], folder, cancel)
    run(ffbase()+['-i', folder/'master.flac', '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', folder/'audio.wav'], folder, cancel)
    with wave.open(str(folder/'audio.wav'), 'rb') as audio_file:
        rate = audio_file.getframerate()
        duration_audio = audio_file.getnframes()/rate
        # 1 ms signed min/max peaks, streamed in bounded blocks.
        with (folder/'waveform.bin').open('wb') as output:
            while raw := audio_file.readframes(rate):
                samples = array.array('h'); samples.frombytes(raw)
                if sys.byteorder != 'little': samples.byteswap()
                peaks = array.array('h')
                for index in range(0, len(samples), 48):
                    block = samples[index:index+48]
                    peaks.extend((min(block), max(block)))
                if sys.byteorder != 'little': peaks.byteswap()
                output.write(peaks.tobytes())
    info = probe(folder/('preview.mp4' if has_video else 'preview.m4a'))
    return min(float(info['format']['duration']), duration_audio, duration)


def prepare(folder, url, start, end, cancel, update):
    update(phase='Reading video information', progress=5)
    info_path = folder/'source-info.json'
    run(ytdlp_base()+['--skip-download', '--dump-single-json', url], folder, cancel, timeout=100, stdout=info_path)
    info = json.loads(info_path.read_text())
    # Expiring source URLs remain local only and are discarded after extracting metadata.
    info_path.unlink()
    duration = info.get('duration')
    if info.get('is_live') or not duration or not math.isfinite(float(duration)):
        raise ValueError('Use a finished video with a known duration, rather than a live stream.')
    start, end, padded_start, padded_end = selection(start, end, float(duration))
    update(title=str(info.get('title') or 'YouTube clip')[:250], source_duration=duration,
           requested_start=start, requested_end=end, origin=padded_start,
           padded_end=padded_end, phase='Downloading the selection plus editing handles', progress=20)
    run(ytdlp_base()+['-f', 'bv*[height<=720][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=720]/bv*[height<=720]+ba/b',
        '--download-sections', f'*{padded_start:.6f}-{padded_end:.6f}', '--force-keyframes-at-cuts',
        '--merge-output-format', 'mp4', '--downloader-args', 'ffmpeg:-threads 2',
        '--postprocessor-args', 'ffmpeg:-threads 2', '--max-filesize', '1G',
        '-o', str(folder/'source.%(ext)s'), url], folder, cancel, timeout=1200)
    files = [p for p in folder.glob('source.*') if p.suffix in ('.mp4', '.mkv', '.webm', '.mov')]
    if len(files) != 1:
        raise RuntimeError('The downloaded segment could not be found.')
    update(phase='Preparing video and waveform', progress=70)
    actual = normalize(folder, files[0], padded_end-padded_start, cancel)
    files[0].unlink()
    if actual < end-padded_start-.1:
        raise RuntimeError('YouTube returned a shorter segment than requested. Try again.')
    update(state='ready', phase='Ready to fine-tune', progress=100, duration=actual,
           cut_start=round(start-padded_start, 3), cut_end=round(min(end-padded_start, actual), 3), error=None)


def cut_bounds(start, end, duration):
    try: start, end = float(start), float(end)
    except (TypeError, ValueError): raise ValueError('Invalid cut positions.') from None
    if not all(math.isfinite(x) for x in (start, end)) or start < 0 or end > duration+.001 or end-start < .02:
        raise ValueError('Keep both markers inside the preview, at least 20 ms apart.')
    return start, min(end, duration)


def export(folder, start, end, duration, fade, filename, cancel, options=None):
    start, end = cut_bounds(start, end, duration)
    o=formats.validate(options or {'fade_in_ms':3 if fade else 0,'fade_out_ms':3 if fade else 0})
    args=ffbase()
    has_master=(folder/'master.flac').exists()
    source=folder/('master.flac' if has_master else 'preview.mp4')
    if o['mode']=='audio':
        args+=['-i',source,'-vn','-map','0:a:0','-af',formats.audio_filter(start,end,o)]
        args+=formats.audio_args(o)
    else:
        args+=['-i',folder/'preview.mp4']
        if has_master and o['include_audio']:args+=['-i',source]
        vf=f'trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS'
        if o['height']!='source':
            height=int(o['height']); width=height*16//9
            vf+=f",scale=w='min({width},iw)':h='min({height},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"
        args+=['-map','0:v:0','-vf',vf]
        if o['format']=='webm':
            args+=['-c:v','libvpx-vp9','-threads','2','-row-mt','1','-deadline','good','-cpu-used','5',
                   '-crf',str({'high':28,'balanced':33,'small':38}[o['quality']]),'-b:v','0']
        else:
            args+=['-c:v','libx264','-threads','2','-preset','fast',
                   '-crf',str({'high':18,'balanced':23,'small':28}[o['quality']])]
        args+=['-pix_fmt','yuv420p']
        if o['include_audio']:
            args+=['-map','1:a:0' if has_master else '0:a:0','-af',formats.audio_filter(start,end,o)]
            args+=formats.audio_args(o,video=True)
        else:args+=['-an']
    args+=['-map_metadata','-1']
    if o['format'] in ('mp4','mov','m4a'):args+=['-movflags','+faststart']
    run(args+[folder/filename],folder,cancel)
    return float(probe(folder/filename)['format']['duration'])


def import_file(folder, source, cancel, update):
    update(phase='Reading uploaded media',progress=10)
    info=probe(source)
    duration=float(info.get('format',{}).get('duration',0))
    if not math.isfinite(duration) or not 0.02<=duration<=MAX_SECONDS:
        raise ValueError('Upload a file between 20 milliseconds and 10 minutes long.')
    audio_only=not any(s['codec_type']=='video' for s in info['streams'])
    update(source_duration=duration,requested_start=0,requested_end=duration,origin=0,
           padded_end=duration,audio_only=audio_only,phase='Preparing waveform and preview',progress=35)
    actual=normalize(folder,source,duration,cancel)
    source.unlink()
    update(state='ready',phase='Ready to convert',progress=100,duration=actual,cut_start=0,
           cut_end=round(actual,3),preview='preview.m4a' if audio_only else 'preview.mp4',error=None)
