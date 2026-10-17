"""Extract existing YouTube captions; no speech models or GPU allocations."""
import html
import json
import math
import re
import time
import media

MAX_BYTES = 8 * 1024**2


def tracks(info):
    result = []
    for kind in ('subtitles', 'automatic_captions'):
        for code, items in info.get(kind, {}).items():
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', code):
                continue
            candidate = next((item for item in items if item.get('ext') == 'json3'), None)
            if candidate:
                result.append({'id': ('auto:' if kind == 'automatic_captions' else 'manual:') + code,
                               'language': code, 'name': str(candidate.get('name') or code)[:100],
                               'automatic': kind == 'automatic_captions'})
    # Prefer source-language captions, then English, then another available language.
    source = str(info.get('language') or '').split('-')[0]
    return sorted(result, key=lambda t: (
        0 if source and t['language'].split('-')[0] == source else
        1 if t['language'].endswith('-orig') else
        2 if t['language'].split('-')[0] == 'en' else 3,
        t['automatic'], t['name'], t['id']))


def parse_json3(data):
    cues = []
    for event in data.get('events', []):
        if not isinstance(event, dict) or not event.get('segs'):
            continue
        text = ' '.join(html.unescape(''.join(str(s.get('utf8', ''))
                                             for s in event['segs'])).split())
        if not text:
            continue
        try:
            start = float(event['tStartMs']) / 1000
            duration = float(event.get('dDurationMs', 0)) / 1000
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in (start, duration)) or start < 0 or duration <= 0:
            continue
        end = start + duration
        if cues and text == cues[-1]['text'] and start < cues[-1]['end']:
            cues[-1]['end'] = max(cues[-1]['end'], end)
        else:
            cues.append({'start': round(start, 3), 'end': round(end, 3), 'text': text})
        if len(cues) > 50000:
            raise ValueError('This transcript is too large to display. Choose a shorter video.')
    cues.sort(key=lambda cue: cue['start'])
    if not cues:
        raise ValueError('YouTube returned an empty transcript for this language. Try another language.')
    return cues


def extract(folder, url, requested, cancel):
    info_path = folder/'caption-info.json'
    try:
        media.run(media.ytdlp_base()+['--skip-download', '--dump-single-json', media.youtube_url(url)],
                  folder, cancel, timeout=100, stdout=info_path)
        info = json.loads(info_path.read_text())
        available = tracks(info)
        if not available:
            raise ValueError('This video has no downloadable captions. Uploaded files and videos without captions need a separate transcription tool.')
        chosen = next((track for track in available if track['id'] == requested), None) if requested else available[0]
        if chosen is None:
            raise ValueError('That caption language is unavailable. Reload the default transcript.')
        flags = ['--write-auto-subs', '--no-write-subs'] if chosen['automatic'] else ['--write-subs', '--no-write-auto-subs']
        media.run(media.ytdlp_base()+['--load-info-json', info_path, '--skip-download',
                  *flags, '--sub-langs', re.escape(chosen['language']), '--sub-format', 'json3',
                  '-o', str(folder/'captions.%(ext)s')], folder, cancel, timeout=100)
        files = list(folder.glob('captions.*.json3'))
        if len(files) != 1:
            raise ValueError('YouTube did not return a caption file. Try another language.')
        if files[0].stat().st_size > MAX_BYTES:
            raise ValueError('This transcript is too large to display. Choose a shorter video.')
        cues = parse_json3(json.loads(files[0].read_text()))
        result = {'track': chosen, 'tracks': available, 'cues': cues, 'revision': time.time_ns()}
        temporary = folder/'transcript.tmp'
        temporary.write_text(json.dumps(result, ensure_ascii=False))
        temporary.replace(folder/'transcript.json')
        return result
    except RuntimeError as exc:
        # Caption requests may fail separately from successful video downloads.
        try: log = (folder/'process.log').read_text(errors='replace')[-5000:]
        except OSError: log = ''
        if '429' in log:
            raise RuntimeError('YouTube is rate-limiting caption downloads. Wait a few minutes, then retry.') from None
        raise RuntimeError('Could not download captions from YouTube. Try again later or choose another language.') from exc
    finally:
        info_path.unlink(missing_ok=True)
        for path in folder.glob('captions.*'):
            path.unlink(missing_ok=True)
