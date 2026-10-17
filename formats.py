"""Validated export profiles. User strings never become FFmpeg options or paths."""
import math
import re

VIDEO_FORMATS = {'mp4','mov','mkv','webm'}
AUDIO_FORMATS = {'mp3','m4a','wav','flac','ogg','opus'}
DEFAULTS = {'mode':'video','format':'mp4','quality':'high','height':'source',
            'bitrate':192,'sample_rate':48000,'channels':'source','volume_db':0,
            'fade_in_ms':3,'fade_out_ms':3,'include_audio':True,'name':'wordcut-clip'}


def validate(raw=None, audio_only=False):
    if raw is not None and not isinstance(raw,dict): raise ValueError('Invalid export settings.')
    data=DEFAULTS | (raw or {})
    if set(data)-set(DEFAULTS): raise ValueError('Unknown export setting.')
    if data['mode'] not in ('video','audio'):raise ValueError('Choose video or audio export.')
    if audio_only and data['mode']=='video':raise ValueError('This source contains audio only. Choose audio export.')
    allowed=VIDEO_FORMATS if data['mode']=='video' else AUDIO_FORMATS
    if not isinstance(data['format'],str) or data['format'] not in allowed:raise ValueError('Unsupported output format.')
    for key,choices in [('quality',('high','balanced','small')),('height',('source','720','480','360')),
                        ('bitrate',(64,96,128,192,256,320)),('sample_rate',(16000,22050,44100,48000)),
                        ('channels',('source','mono','stereo'))]:
        if isinstance(data[key],bool) or data[key] not in choices:raise ValueError('Invalid '+key.replace('_',' ')+'.')
    if data['format']=='mp3' and data['sample_rate']<32000 and data['bitrate']>128:
        raise ValueError('For MP3 below 32 kHz, choose a bitrate up to 128 kbps.')
    if data['format']=='opus' and data['sample_rate'] not in (16000,48000):
        raise ValueError('Opus supports 16 or 48 kHz in this editor.')
    for key,low,high in [('volume_db',-30,12),('fade_in_ms',0,2000),('fade_out_ms',0,2000)]:
        value=data[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError('Invalid '+key.replace('_',' ')+'.')
    if not isinstance(data['include_audio'],bool):raise ValueError('Invalid audio track setting.')
    if not isinstance(data['name'],str):raise ValueError('Invalid filename.')
    # Conservative ASCII attachment filename, never used as a filesystem path.
    name=re.sub(r'[^A-Za-z0-9 _.-]','_',data['name']).strip(' .')[:80]
    name=re.sub(r'\.(mp4|mov|mkv|webm|mp3|m4a|wav|flac|ogg|opus)$','',name,flags=re.I)
    data['name']=name or 'wordcut-clip'
    return data


def audio_args(o, video=False):
    fmt=o['format']
    codec=('libopus' if fmt=='webm' else 'aac') if video else {
        'mp3':'libmp3lame','m4a':'aac','wav':'pcm_s16le','flac':'flac','ogg':'libvorbis','opus':'libopus'}[fmt]
    rate=48000 if video and fmt=='webm' else o['sample_rate']
    args=['-c:a',codec,'-ar',str(rate)]
    if codec=='libvorbis':args+=['-q:a',str({'high':7,'balanced':5,'small':3}[o['quality']])]
    elif codec not in ('flac','pcm_s16le'):args+=['-b:a',str(o['bitrate'])+'k']
    if codec=='flac':args+=['-sample_fmt','s32','-compression_level','5']
    if o['channels']!='source':args+=['-ac','1' if o['channels']=='mono' else '2']
    return args


def audio_filter(start,end,o):
    result=f'atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS'
    if o['volume_db']:result+=f",volume={o['volume_db']:.3f}dB"
    # Don't overlap two long fades on a short selection.
    fade_in=min(o['fade_in_ms']/1000,(end-start)/2)
    fade_out=min(o['fade_out_ms']/1000,(end-start)/2)
    if fade_in:result+=f',afade=t=in:d={fade_in:.6f}'
    if fade_out:result+=f',afade=t=out:st={end-start-fade_out:.6f}:d={fade_out:.6f}'
    return result
