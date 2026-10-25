"""Local FFmpeg editing, word-synchronized ASS captions, and safe framing."""
import json
import math
from pathlib import Path
import re
import subprocess
from . import media


def probe(path):
    result=subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)])
    data=json.loads(result)
    video=next((s for s in data['streams'] if s['codec_type']=='video'),None)
    if not video:raise ValueError('Source has no video stream')
    a,b=video.get('avg_frame_rate','30/1').split('/')
    return dict(width=video['width'],height=video['height'],fps=float(a)/max(1,float(b)),
        audio=any(s['codec_type']=='audio' for s in data['streams']),duration=float(data['format']['duration']))


def ass_time(t):
    n=max(0,round(t*100));return f'{n//360000}:{n//6000%60:02d}:{n//100%60:02d}.{n%100:02d}'


def clean(text):
    # Treat all transcript and AI strings as plain text, never ASS override commands.
    return str(text).replace('\\',' ').replace('{','(').replace('}',')').replace('\n',' ')


def subtitle_groups(words,start,end):
    groups=[];group=[]
    for word in words:
        if word['end']<=start or word['start']>=end:continue
        item=dict(word=word['word'],start=max(0,word['start']-start),end=min(end-start,word['end']-start))
        if group and (len(group)>=5 or sum(len(w['word'])+1 for w in group)+len(item['word'])>34 or item['start']-group[-1]['end']>.55):
            groups.append(group);group=[]
        group.append(item)
        if re.search(r'[.!?]$',item['word']):groups.append(group);group=[]
    if group:groups.append(group)
    return groups


def write_captions(folder,groups,width,height,animated,hook='',context='',emoji=''):
    fontsize=38 if width==720 else 30
    header=f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,{fontsize},&H00FFFFFF,&H0000DDFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,38,38,62,1
Style: Hook,Arial,{fontsize if height==1280 else 24},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,8,42,42,30,1
Style: Context,Arial,{max(22,fontsize-10)},&H00DBE9ED,&H00FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,1,1,8,42,42,{140 if height==1280 else 100},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines=[];srt=[];word_rows=[]
    for number,group in enumerate(groups,1):
        text=' '.join(clean(w['word']) for w in group)
        a,b=group[0]['start'],group[-1]['end']
        if animated:
            cursor=a
            for index,w in enumerate(group):
                if w['start']>cursor:
                    lines.append(f'Dialogue: 0,{ass_time(cursor)},{ass_time(w["start"])},Caption,,0,0,0,,{text}')
                parts=[('{\\c&H005EEFCB&\\fscx108\\fscy108}'+clean(x['word'])+'{\\rCaption}') if i==index else clean(x['word']) for i,x in enumerate(group)]
                active=' '.join(parts)
                lines.append(f'Dialogue: 1,{ass_time(w["start"])},{ass_time(w["end"])},Caption,,0,0,0,,{{\\bord4}}{active}')
                cursor=w['end']
        else:
            lines.append(f'Dialogue: 0,{ass_time(a)},{ass_time(b)},Caption,,0,0,0,,{text}')
        def stamp(x):
            n=round(x*1000);return f'{n//3600000:02d}:{n//60000%60:02d}:{n//1000%60:02d},{n%1000:03d}'
        srt.append(f'{number}\n{stamp(a)} --> {stamp(b)}\n{text}\n')
        word_rows.extend(group)
    if hook:lines.append(f'Dialogue: 2,0:00:00.00,0:00:04.00,Hook,,0,0,0,,{clean(hook)}')
    if context:lines.append(f'Dialogue: 2,0:00:00.00,0:00:06.00,Context,,0,0,0,,{clean(context)}')
    if emoji:
        glyph={'idea':'💡','laugh':'😂','heart':'❤','fire':'🔥','pin':'📌'}.get(emoji,'')
        if glyph:lines.append(f'Dialogue: 2,0:00:00.00,0:00:03.00,Hook,,0,0,0,,{{\\fnSegoe UI Emoji\\pos({width-70},180)}}{glyph}')
    (folder/'captions.ass').write_text(header+'\n'.join(lines)+'\n',encoding='utf-8-sig')
    (folder/'subtitles.srt').write_text('\n'.join(srt),encoding='utf-8')
    (folder/'words.json').write_text(json.dumps(word_rows,indent=2),encoding='utf-8')


def framing(source,start,end,options,meta):
    vertical=options.vertical
    width,height=(720,1280) if vertical else (960,640)
    # Reserve separate bands for text: no face or source content is covered in safe-fit mode.
    content_h=840 if vertical else 320
    content_y=250 if vertical else 170
    warnings=[]
    plan=dict(mode='safe-fit',faces_detected=0,subject_verified=False,
              content_box=[0,content_y,width,content_h],width=width,height=height)
    crop=None
    if vertical and options.crop=='face':
        try:
            import cv2
            cascade=cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
            cap=cv2.VideoCapture(str(source));faces=[];sampled=0;hits=0
            count=min(40,max(3,math.ceil((end-start)/2)))
            for i in range(count):
                cap.set(cv2.CAP_PROP_POS_MSEC,(start+(end-start)*(i+.5)/count)*1000)
                ok,frame=cap.read()
                if not ok:continue
                sampled+=1
                scale=480/frame.shape[1]
                gray=cv2.cvtColor(cv2.resize(frame,(480,round(frame.shape[0]*scale))),cv2.COLOR_BGR2GRAY)
                found=cascade.detectMultiScale(gray,1.1,5,minSize=(22,22))
                if len(found):hits+=1
                for x,y,w,h in found:faces.append((x/scale,y/scale,(x+w)/scale,(y+h)/scale))
            cap.release()
            plan.update(faces_detected=len(faces),frames_sampled=sampled,face_frames=hits)
            cw=min(meta['width'],int(meta['height']*width/content_h)//2*2)
            if faces and hits>=sampled*.7:
                left=min(f[0] for f in faces);right=max(f[2] for f in faces)
                # Require the full detected-face envelope and a 15% safety margin.
                center=(left+right)/2
                x=max(0,min(meta['width']-cw,round(center-cw/2)))//2*2
                if left>=x+cw*.08 and right<=x+cw*.92:
                    crop=f'crop={cw}:{meta["height"]}:{x}:0'
                    plan.update(mode='face-envelope-crop',subject_verified=True,crop_box=[x,0,cw,meta['height']])
            if not crop:warnings.append('Face crop was not reliable across sampled frames; safe-fit preserves the entire source instead.')
        except ImportError:warnings.append('OpenCV is not installed; safe-fit preserves the entire source. No subject-aware crop was claimed.')
    if options.zoom:
        warnings.append('Smart zoom is limited to verified face framing; it is skipped for safe-fit or uncertain subjects.')
    filters=[]
    if crop:filters.append(crop)
    filters += [f'scale={width}:{content_h}:force_original_aspect_ratio=decrease:force_divisible_by=2',
        f'pad={width}:{content_h}:(ow-iw)/2:(oh-ih)/2:color=0x111821',
        f'pad={width}:{height}:0:{content_y}:color=0x111821','setsar=1']
    if options.zoom and plan['subject_verified']:
        # Small centered push-in only after face envelope safety check, bounded to 2%.
        filters=[crop,f'scale={width*2}:{content_h*2}',
            f"zoompan=z='1+0.02*min(on/90,1)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{content_h}:fps=30",
            f'pad={width}:{height}:0:{content_y}:color=0x111821','setsar=1']
        plan['zoom']='1.00 to 1.02; face envelope retains safety margin'
    return filters,plan,warnings


def scene_times(source,start,end):
    """Measure real frame changes locally; return clip-relative cut times."""
    command=['ffmpeg','-hide_banner','-loglevel','info','-ss',str(start),'-i',str(source),
        '-t',str(end-start),'-an','-vf',"scale=160:-2,setpts=PTS-STARTPTS,select='gt(scene,0.35)',showinfo",'-f','null','-']
    process=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=300)
    if process.returncode:raise RuntimeError('Local scene-change scan failed')
    found=[]
    for value in re.findall(r'pts_time:([0-9.]+)',process.stderr):
        t=float(value)
        if .3<t<end-start-.3 and (not found or t-found[-1]>.5):found.append(t)
    return found[:100]


def render(source,clip,folder,options,words,analysis,progress):
    folder.mkdir(parents=True,exist_ok=True)
    meta=probe(source)
    start,end=clip['start'],clip['end'];duration=end-start
    groups=subtitle_groups(words,start,end) if options.subtitles or options.animated else []
    vf,plan,warnings=framing(source,start,end,options,meta)
    if not options.vertical and not any((options.subtitles,options.animated,options.hooks,options.context,options.emoji)):
        vf=['scale=trunc(iw/2)*2:trunc(ih/2)*2','setsar=1']
        plan.update(mode='original',width=meta['width'],height=meta['height'])
    if options.transitions:
        # Dips at actual cuts preserve the entire timeline; no footage is crossfaded away.
        vf += ['fade=t=in:st=0:d=0.12',f'fade=t=out:st={max(0,duration-.12):.6f}:d=0.12']
        cuts=scene_times(source,start,end)
        plan['scene_transitions']=cuts
        for cut in cuts:
            vf += [f"fade=t=out:st={cut-.08:.6f}:d=0.08:enable='between(t,{cut-.08:.6f},{cut:.6f})'",
                f"fade=t=in:st={cut:.6f}:d=0.08:enable='between(t,{cut:.6f},{cut+.08:.6f})'"]
        if not cuts:warnings.append('No strong scene changes detected; only gentle clip-edge fades were applied.')
    hook=analysis.get('hook','') if options.hooks else ''
    context=analysis.get('context','') if options.context else ''
    emoji=analysis.get('emoji','') if options.emoji else ''
    if emoji and not Path('C:/Windows/Fonts/seguiemj.ttf').is_file():
        warnings.append('No compatible emoji font was found; emoji overlay was skipped rather than rendering a missing glyph.')
        emoji=''
    if options.hooks and not hook:warnings.append('No grounded AI hook was available; no hook text was invented.')
    if options.context and not context:warnings.append('No grounded AI context quote was available; no context was invented.')
    write_captions(folder,groups,plan['width'],plan['height'],options.animated,hook,context,emoji)
    if groups or hook or context or emoji:vf.append('ass=captions.ass')
    command=['ffmpeg','-hide_banner','-loglevel','error','-y','-ss',f'{start:.6f}','-i',str(source),
        '-t',f'{duration:.6f}','-map','0:v:0','-map','0:a:0?', '-vf',','.join(vf),
        '-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p',
        '-c:a','aac','-b:a','160k','-movflags','+faststart','-progress','pipe:1','clip.mp4']
    with (folder/'render.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen(command,cwd=folder,stdout=subprocess.PIPE,stderr=log,text=True,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with process.stdout:
                for line in process.stdout:
                    if line.startswith('out_time_us='):
                        try:progress(min(1,float(line.split('=',1)[1])/1e6/duration))
                        except ValueError:pass
            if process.wait():raise RuntimeError('FFmpeg render failed: '+(folder/'render.log').read_text(encoding='utf-8')[-1200:])
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    actual=probe(folder/'clip.mp4')
    if options.thumbnail:
        code,_=media.run(['ffmpeg','-v','error','-y','-ss',str(min(1,duration/2)),
            '-i',str(folder/'clip.mp4'),'-frames:v','1','-vf','scale=360:-2',str(folder/'thumbnail.jpg')])
        if code:warnings.append('Thumbnail generation failed')
    return dict(**clip,actual_duration=actual['duration'],width=actual['width'],height=actual['height'],
        hook=hook,context=context,emoji=emoji,framing=plan,warnings=warnings,
        caption_groups=len(groups),caption_words=sum(len(g) for g in groups),analysis=analysis)
