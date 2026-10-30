"""Designed thumbnails from an actual video frame, using free local models only.

prepare() picks the frame whose main face is the most expressive while turned
toward the camera, and cuts the person out. render() composes that cutout with
bold text in one of three styles. Models come from
tools/prepare_thumbnail_design_models.py and are never downloaded here:
- YuNet face detector (OpenCV Zoo, MIT)
- FER+ facial expression classifier (ONNX Model Zoo, MIT)
- ISNet general-use cutout model, the free model rembg uses (Apache-2.0)
Eye gaze is not modelled: "facing the camera" is judged from head pose.
"""
from functools import lru_cache
from pathlib import Path
from . import media
from .resource_paths import MODELS

FOLDER=MODELS/'thumbnail-design'
FACE_MODEL=FOLDER/'face_detection_yunet_2023mar.onnx'
EXPRESSION_MODEL=FOLDER/'emotion-ferplus-8.onnx'
CUTOUT_MODEL=FOLDER/'isnet-general-use.onnx'
W,H=1280,720
SAMPLES=60
EMOTIONS=('neutral','happiness','surprise','sadness','anger','disgust','fear','contempt')
FONT_DIRS=(Path('C:/Windows/Fonts'),Path('/usr/share/fonts/truetype/dejavu'),Path('/usr/share/fonts/TTF'))
# face: face height as a share of the image (at least 40%). Each style has at
# most three elements: the person, the text and, for Mystery, the text's glow.
STYLES={
    'mrbeast':dict(label='MrBeast',fonts=('impact.ttf','ariblk.ttf','DejaVuSans-Bold.ttf'),face=.50,
        top=(0,214,255),bottom=(0,82,255),scene=.35,accent=(255,226,0),glow=.85,outline=12,color=1.45,contrast=1.15),
    'mkbhd':dict(label='MKBHD',fonts=('seguibl.ttf','ariblk.ttf','DejaVuSans-Bold.ttf'),face=.44,
        top=(30,30,36),bottom=(6,6,8),scene=0,accent=(236,32,44),glow=.5,outline=7,color=1.1,contrast=1.12),
    'mystery':dict(label='Mystery',fonts=('impact.ttf','ariblk.ttf','DejaVuSans-Bold.ttf'),face=.46,
        top=(12,10,22),bottom=(0,0,0),scene=.55,accent=(255,64,40),glow=.3,outline=7,color=.9,contrast=1.25),
}

def status():
    missing=[path.name for path in (FACE_MODEL,EXPRESSION_MODEL,CUTOUT_MODEL) if not path.is_file()]
    try:
        import cv2,onnxruntime,PIL  # noqa: F401
        libraries=True
    except ImportError:libraries=False
    ready=libraries and not missing
    reason=None if ready else ('Install pillow, opencv-python-headless and onnxruntime.' if not libraries else
        'Missing local models: '+', '.join(missing)+'. Run tools/prepare_thumbnail_design_models.py.')
    return dict(ready=ready,styles={key:style['label'] for key,style in STYLES.items()},reason=reason)

@lru_cache(maxsize=1)
def _sessions():
    import onnxruntime
    options=onnxruntime.SessionOptions();options.intra_op_num_threads=2
    return tuple(onnxruntime.InferenceSession(str(path),options,providers=['CPUExecutionProvider']) for path in (EXPRESSION_MODEL,CUTOUT_MODEL))

@lru_cache(maxsize=1)
def _detector():
    import cv2
    return cv2.FaceDetectorYN.create(str(FACE_MODEL),'',(320,320),0.8)

def _faces(image):
    """Faces in a BGR image, largest first: box (x, y, w, h), 5 landmarks, detector score."""
    detector=_detector();detector.setInputSize((image.shape[1],image.shape[0]))
    _,rows=detector.detect(image)
    faces=[dict(box=[float(v) for v in row[:4]],landmarks=[[float(row[i]),float(row[i+1])] for i in range(4,14,2)],score=float(row[14]))
           for row in (rows if rows is not None else [])]
    return sorted(faces,key=lambda face:face['box'][2]*face['box'][3],reverse=True)

def facing(face):
    """1 when the head faces the camera, falling to 0 as it turns, tilts or nods (5-point head pose)."""
    import numpy as np
    right_eye,left_eye,nose,mouth_right,mouth_left=map(np.array,face['landmarks'])
    eyes=(right_eye+left_eye)/2;mouth=(mouth_right+mouth_left)/2;span=np.linalg.norm(left_eye-right_eye)
    if span<1 or mouth[1]<=eyes[1]:return 0.
    yaw=abs(nose[0]-eyes[0])/span
    roll=abs(np.degrees(np.arctan2(left_eye[1]-right_eye[1],left_eye[0]-right_eye[0])))
    pitch=abs((nose[1]-eyes[1])/(mouth[1]-eyes[1])-.55)
    return float(max(0,1-yaw/.35)*max(0,1-roll/25)*max(0,1-pitch/.35))

def expression(gray,face):
    """FER+ probabilities for the face crop."""
    import numpy as np
    import cv2
    x,y,w,h=face['box'];side=max(w,h);cx,cy=x+w/2,y+h/2
    x0,y0=int(max(0,cx-side/2)),int(max(0,cy-side/2))
    crop=gray[y0:int(cy+side/2),x0:int(cx+side/2)]
    if crop.size==0:return dict.fromkeys(EMOTIONS,0.)|dict(neutral=1.)
    session=_sessions()[0]
    logits=session.run(None,{session.get_inputs()[0].name:cv2.resize(crop,(64,64)).astype(np.float32)[None,None]})[0][0]
    odds=np.exp(logits-logits.max());odds/=odds.sum()
    return dict(zip(EMOTIONS,map(float,odds)))

def score(image,faces):
    """Expressiveness x facing the camera x sharpness x size for the frame's main face, favouring
    frames where that person is alone (one cutout) and has room below the face (no cut-off body)."""
    import cv2
    face=faces[0];gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    emotions=expression(gray,face);turned=facing(face)
    x,y,w,h=map(int,face['box'])
    sharp=cv2.Laplacian(gray[max(0,y):y+h,max(0,x):x+w],cv2.CV_64F).var() if w>0 and h>0 else 0
    size=min(1.,h/image.shape[0]/.25)**.5
    alone=w*h/sum(f['box'][2]*f['box'][3] for f in faces)
    room=min(1.,max(.2,(.9-(y+h)/image.shape[0])/.3))
    expressive=1-emotions['neutral']
    total=(.15+expressive)*turned*(.5+.5*min(1.,sharp/120))*size*alone*room
    return dict(score=total,expressive=expressive,facing=turned,emotion=max(emotions,key=emotions.get))

def _frame(video,time,path,width=None):
    scale=['-vf',f'scale={width}:-2'] if width else []
    code,_=media.run(['ffmpeg','-v','error','-y','-ss',f'{time:.3f}','-i',str(video),'-frames:v','1',*scale,str(path)],timeout=60)
    if code or not path.is_file():raise ValueError('Could not read a frame from the video')

def cutout(rgb):
    """Person alpha 0..1 from ISNet, preprocessed exactly as rembg does."""
    import numpy as np
    import cv2
    session=_sessions()[1]
    pixels=cv2.resize(rgb,(1024,1024),interpolation=cv2.INTER_LANCZOS4).astype(np.float32)
    pixels=pixels/max(pixels.max(),1e-6)-np.array([.485,.456,.406],np.float32)
    mask=session.run(None,{session.get_inputs()[0].name:pixels.transpose(2,0,1)[None]})[0][0,0]
    mask=(mask-mask.min())/max(mask.max()-mask.min(),1e-6)
    return cv2.resize(mask,(rgb.shape[1],rgb.shape[0]),interpolation=cv2.INTER_LINEAR)

def keep_person(alpha,face):
    """Only the cutout region that contains the chosen face."""
    import numpy as np
    import cv2
    count,labels=cv2.connectedComponents((alpha>.5).astype(np.uint8))
    x,y,w,h=map(int,face['box'])
    centre=labels[min(labels.shape[0]-1,y+h//2),min(labels.shape[1]-1,x+w//2)]
    if centre==0:
        if count<2:raise ValueError('The cutout did not find the person in the chosen frame')
        centre=1+int(np.argmax(np.bincount(labels.ravel())[1:]))
    region=cv2.dilate((labels==centre).astype(np.uint8),cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(15,15)))
    return alpha*region

def prepare(video,duration,folder):
    """Choose the best face frame and save it with its cutout in folder. -> frame details."""
    import numpy as np
    import cv2
    folder=Path(folder);samples=folder/'design-samples';samples.mkdir(parents=True,exist_ok=True)
    best=None
    try:
        for i in range(SAMPLES):
            time=duration*(i+.5)/SAMPLES;path=samples/f'{i}.jpg'
            _frame(video,time,path,640)
            image=cv2.imread(str(path));faces=_faces(image)
            if not faces:continue
            result=score(image,faces)|dict(time=round(time,2))
            if best is None or result['score']>best['score']:best=result
    finally:
        for path in samples.glob('*.jpg'):path.unlink()
        samples.rmdir()
    if best is None or best['facing']==0:raise ValueError('No clear face turned toward the camera was found; designed thumbnails need a visible person.')
    frame=folder/'design-frame.png';_frame(video,best['time'],frame)
    image=cv2.imread(str(frame));faces=_faces(image)
    if not faces:raise ValueError('The chosen face could not be found again at full size')
    alpha=keep_person(cutout(cv2.cvtColor(image,cv2.COLOR_BGR2RGB)),faces[0])
    cv2.imwrite(str(folder/'design-alpha.png'),(alpha*255).astype(np.uint8))
    return dict(time=best['time'],face=faces[0]['box'],emotion=best['emotion'],
                expressive=round(best['expressive'],3),facing=round(best['facing'],3))

def _font(style,size):
    from PIL import ImageFont
    for name in STYLES[style]['fonts']:
        for directory in FONT_DIRS:
            if (directory/name).is_file():return ImageFont.truetype(str(directory/name),size)
    return ImageFont.load_default(size)

def _gradient(top,bottom):
    import numpy as np
    from PIL import Image
    t=np.linspace(0,1,H,dtype=np.float32)[:,None,None]
    rows=np.array(top,np.float32)*(1-t)+np.array(bottom,np.float32)*t
    return Image.fromarray(np.broadcast_to(rows,(H,W,3)).astype(np.uint8))

def _vignette(image,strength):
    import numpy as np
    from PIL import Image
    y,x=np.ogrid[-1:1:H*1j,-1:1:W*1j]
    shade=np.clip(1-strength*(x**2+y**2)/2,0,1)[...,None]
    return Image.fromarray((np.asarray(image,np.float32)*shade).astype(np.uint8))

def _layout(style,words,width,height):
    """Biggest font size and line split for words inside width x height."""
    best=None
    for split in range(1,len(words)+1):
        lines=[words[:split],words[split:]] if split<len(words) else [words]
        for size in range(int(height*.62),20,-4):
            font=_font(style,size);stroke=max(4,size//11)
            widths=[font.getlength(' '.join(line))+2*stroke for line in lines]
            if max(widths)<=width and len(lines)*size*1.02<=height:
                if best is None or size>best[0]:best=(size,lines)
                break
    return best or (24,[words])

def render(folder,frame,words,key,style):
    """1280x720 designed thumbnail (PIL image) for a frame from prepare()."""
    import numpy as np
    import cv2
    from PIL import Image,ImageDraw,ImageEnhance,ImageFilter
    look=STYLES[style];folder=Path(folder)
    rgb=cv2.cvtColor(cv2.imread(str(folder/'design-frame.png')),cv2.COLOR_BGR2RGB)
    alpha=cv2.imread(str(folder/'design-alpha.png'),cv2.IMREAD_GRAYSCALE)
    fx,fy,fw,fh=frame['face']
    # Face at least 40% of the height, on the right; text on the left never reaches it.
    scale=look['face']*H/fh
    size=(round(rgb.shape[1]*scale),round(rgb.shape[0]*scale))
    person=Image.fromarray(rgb).resize(size,Image.LANCZOS)
    person=ImageEnhance.Sharpness(ImageEnhance.Color(ImageEnhance.Contrast(person).enhance(look['contrast'])).enhance(look['color'])).enhance(1.4)
    mask=Image.fromarray(alpha).resize(size,Image.LANCZOS)
    face_w=fw*scale;left=min(W*.97-face_w,max(W*.47,W*.70-face_w/2))
    ox=round(left-fx*scale);oy=round(H*.08-fy*scale)
    rows=np.nonzero(np.asarray(mask).max(axis=1)>127)[0]
    if len(rows) and rows[-1]+oy<H:oy=min(H-int(rows[-1]),round(H*.98-(fy+fh)*scale))  # no floating body; whole face stays in
    face_box=(left,oy+fy*scale,left+face_w,oy+(fy+fh)*scale)
    # Background
    canvas=_gradient(look['top'],look['bottom'])
    if look['scene']:
        scene=Image.fromarray(rgb).resize((W,H),Image.LANCZOS).filter(ImageFilter.GaussianBlur(18))
        scene=ImageEnhance.Color(scene).enhance(2.0 if style=='mrbeast' else .5)
        scene=ImageEnhance.Brightness(scene).enhance(1.15 if style=='mrbeast' else .45)
        canvas=Image.blend(canvas,scene,look['scene'])
    if style!='mrbeast':canvas=_vignette(canvas,.9)
    # Person with a white outline and a soft glow
    full=Image.new('L',(W,H));full.paste(mask,(ox,oy))
    solid=np.asarray(full)>127
    ring=cv2.dilate(solid.astype(np.uint8)*255,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*look['outline']+1,)*2))
    glow=Image.fromarray(ring).filter(ImageFilter.GaussianBlur(28)).point(lambda v:int(v*look['glow']))
    canvas.paste((255,255,255),(0,0),glow)
    canvas.paste(Image.new('RGB',(W,H),(255,255,255)),(0,0),Image.fromarray(ring).filter(ImageFilter.GaussianBlur(1.5)))
    canvas.paste(person,(ox,oy),mask)
    # Text: 2-4 huge words, thick black outline, one key word in the accent colour
    words=[w.upper() for w in words];region=(round(W*.04),round(H*.08),round(face_box[0]-W*.03),round(H*.92))
    font_size,lines=_layout(style,words,region[2]-region[0],region[3]-region[1])
    font=_font(style,font_size);stroke=max(4,font_size//11);line_h=font_size*1.02
    y=region[1]+(region[3]-region[1]-len(lines)*line_h)/2
    shadow=Image.new('L',(W,H));glow_layer=Image.new('L',(W,H));placed=[];index=0
    for line in lines:
        x=region[0]
        for word in line:
            placed.append((x,y,word,index==key));ImageDraw.Draw(shadow).text((x+6,y+8),word,font=font,fill=200,stroke_width=stroke)
            if index==key:ImageDraw.Draw(glow_layer).text((x,y),word,font=font,fill=255,stroke_width=stroke)
            x+=font.getlength(word+' ');index+=1
        y+=line_h
    canvas.paste((0,0,0),(0,0),shadow.filter(ImageFilter.GaussianBlur(6)))
    if style=='mystery':
        halo=glow_layer.filter(ImageFilter.GaussianBlur(font_size*.18))
        for _ in range(2):canvas.paste(look['accent'],(0,0),halo)
    draw=ImageDraw.Draw(canvas)
    for x,y,word,accent in placed:
        draw.text((x,y),word,font=font,fill=look['accent'] if accent else (255,255,255),stroke_width=stroke,stroke_fill=(0,0,0))
    # Pop at phone size
    canvas=ImageEnhance.Contrast(canvas).enhance(1.08)
    canvas=ImageEnhance.Color(canvas).enhance(1.15 if style=='mrbeast' else 1.0)
    canvas=canvas.filter(ImageFilter.UnsharpMask(radius=2,percent=60,threshold=2))
    text_right=max(x+font.getlength(word)+stroke for x,_,word,_ in placed)
    if text_right>face_box[0]:raise ValueError('Text would cover the face')
    return canvas
