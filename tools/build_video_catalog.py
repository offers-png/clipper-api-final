"""Build the reviewed catalog from cached official schemas and public price rows.

Offline build step; never used to submit jobs or discover/replace models at runtime.
"""
import json,re,yaml
from pathlib import Path
from decimal import Decimal

ROOT=Path('clipforge_studios/data/pricing-research')
PRICES=json.loads((ROOT/'public-video-prices.json').read_text(encoding='utf-8'))
MODELS=[]
def schema(path):
    text=(ROOT/('docs.kie.ai_'+path.replace('/','_')+'.md.txt')).read_text(encoding='utf-8')
    data=yaml.safe_load(re.search(r'^```yaml\s*\n(.*?)^```',text,re.S|re.M)[1])
    operation=data['paths']['/api/v1/jobs/createTask']['post']
    props=operation['requestBody']['content']['application/json']['schema']['properties']
    inp=props['input'];inp=inp.get('oneOf',[inp])[0]
    return props,inp

def add(path,provider,name,modes,durations,price_prefix,resolutions,audio='model',fixed_resolution=None,price_filter='',audio_field=None,variant=None):
    props,inp=schema(path);fields=inp['properties']
    for mode in modes:
        prices=[]
        for row in PRICES:
            desc=row['modelDescription'].lower()
            if not desc.startswith(price_prefix.lower()) or (price_filter and price_filter.lower() not in desc):continue
            # Require the correct input route when a pricing family has multiple routes.
            route=re.search(r'(text[ -]to[ -]video|image[ -]to[ -]video)',desc)
            if route and 'text /image' not in desc and not route[0].startswith(mode):continue
            if any(x in desc and x not in price_prefix.lower() for x in ('with video','reference','transform','extend','upscale','video-edit','videoedit','r2v')):continue
            res=next((r for r in resolutions if re.search(r'(?<!\w)'+re.escape(r.lower())+r'(?!\w)',desc)),fixed_resolution)
            if res not in resolutions:continue
            aud=('off' if any(x in desc for x in ('no audio','without audio','no native audio')) else 'on') if audio=='optional' and audio_field else 'model'
            # When optional audio does not alter pricing, publish both explicit choices.
            auds=['off','on'] if audio=='optional' and not any(x in desc for x in ('no audio','without audio','with audio','native audio')) else [aud]
            ds=re.search(r'(\d+(?:\.0)?)s',desc)
            ds=[int(float(ds[1]))] if ds else durations
            for a in auds:
                for duration in ds:
                    if duration not in durations:continue
                    credits=Decimal(row['creditPrice'])*(duration if row['creditUnit']=='per second' else 1)
                    if row['creditUnit'] not in ('per second','per video'):continue
                    prices.append(dict(resolution=res,audio=a,duration=duration,credits=format(credits.normalize(),'f'),source_description=row['modelDescription'],source_credits=row['creditPrice'],source_unit=row['creditUnit']))
        # Exact duplicate rows are not a valid reason to price twice; conflicting rows fail closed.
        unique={}
        for p in prices:
            key=(p['resolution'],p['audio'],p['duration'])
            if key in unique and Decimal(unique[key]['credits'])!=Decimal(p['credits']):raise ValueError(('Conflicting official prices',provider,key))
            unique[key]=p
        if not unique:raise ValueError(('No verified price',provider,mode))
        rid=provider if len(modes)==1 else provider+'::'+mode
        if variant:rid+='::'+variant
        field_keys=['prompt','duration','resolution','quality','ratio','aspect_ratio','image_url','image_urls','first_frame_url','sound','audio','generate_audio','generate_audio_switch','enable_audio','mode','model','generation_type','enable_fallback','multi_shots']
        allowed={k:{x:v for x,v in fields[k].items() if x in ('type','enum','minLength','maxLength','minimum','maximum')} for k in field_keys if k in fields}
        prompt=fields.get('prompt',{});limit=prompt.get('maxLength')
        if not limit:
            m=re.search(r'(?:max(?:imum)?(?: length)?[: ]*|up to )([\d,]+) characters',prompt.get('description',''),re.I)
            limit=int(m[1].replace(',','')) if m else 2000
        ratio_enum=fields.get('ratio',fields.get('aspect_ratio',{})).get('enum',['16:9','9:16','1:1'])
        ratios=[r for r in ['16:9','9:16','1:1'] if r in ratio_enum]
        if not ratios:ratios=['16:9','9:16','1:1']
        MODELS.append(dict(id=rid,provider_model=provider,variant=variant,name=name,mode=mode,durations=sorted({p['duration'] for p in unique.values()}),
            ratios=ratios,schema_required=inp.get('required',[]),
            resolutions=list(dict.fromkeys(p['resolution'] for p in unique.values())),audio=audio,audio_field=audio_field,
            prompt_limit=min(limit,20000),schema_fields=allowed,prices=list(unique.values()),
            schema_source='https://docs.kie.ai/'+path,pricing_source='https://kie.ai/pricing',pricing_updated='2026-10-26',pricing_method='verified_snapshot'))

for version in ('2-5','2-6','2-7'):
    for mode in ('text','image'):
        path=f'market/wan/{version}-{mode}-to-video';model=f'wan/{version}-{mode}-to-video'
        add(path,model,'Wan '+version.replace('-','.'),[mode],list(range(2,16)) if version=='2-7' else [5,10,15] if version=='2-6' else [5,10],
            'wan '+version.replace('-','.')+(' video,' if version=='2-7' else ','),['720p','1080p'])
add('market/wan/2-6-flash-image-to-video','wan/2-6-flash-image-to-video','Wan 2.6 Flash',['image'],[5,10,15],'wan 2.6 flash,',['720p','1080p'],'optional',audio_field='audio')
for version in ('3-0-video','3-0-video-prime'):
    add('market/wan/'+version,'wan/'+version,'Wan '+('3.0 Prime' if 'prime' in version else '3.0'),['text','image'],list(range(2,31)),
        'wan3.0 video prime,' if 'prime' in version else 'wan 3.0 video,',['480P','720P','1080P'],'optional',audio_field='audio')
for version in ('v6','v6-max'):
    for mode in ('text','image'):
        add(f'market/pixverse/{"v6-max-" if version=="v6-max" else ""}{mode}-to-video',f'pixverse-{version}/{mode}-to-video','PixVerse '+version.upper(),[mode],list(range(1,16)),f'pixverse-{version}, Text /Image to Video,',['360p','540p','720p','1080p'],'optional',audio_field='generate_audio_switch')
for mode in ('text','image'):
    add('market/grok-imagine/'+mode+'-to-video','grok-imagine/'+mode+'-to-video','Grok Imagine',[mode],list(range(6,31)),'grok-imagine,',['480p','720p','1080p'],'included')
for mode in ('text','image'):
    for version in ('v1-lite','v1-pro'):
        add(f'market/bytedance/{version}-{mode}-to-video',f'bytedance/{version}-{mode}-to-video','Seedance '+version.replace('v1','1').title(),[mode],[5,10],f'bytedance/seedance-{version},',['480p','720p','1080p'],'silent')
add('market/bytedance/v1-pro-fast-image-to-video','bytedance/v1-pro-fast-image-to-video','Seedance 1 Pro Fast',['image'],[5,10],'bytedance/seedance-v1-pro-fast,',['720p','1080p'],'silent')
for version in ('seedance-2','seedance-2-fast','seedance-2-mini','seedance-2-5'):
    prefix='bytedance/seedance-2 fast,' if version=='seedance-2-fast' else 'bytedance/'+version+','
    add('market/bytedance/'+version,'bytedance/'+version,version.replace('seedance','Seedance').replace('-',' ').title(),['text','image'],list(range(4,31 if version=='seedance-2-5' else 16)),prefix,
        ['480p','720p','1080p','4k'],'optional',price_filter='no video',audio_field='generate_audio')
for mode in ('text','image'):
    add('market/kling/'+mode+'-to-video','kling-2.6/'+mode+'-to-video','Kling 2.6',[mode],[5,10],'kling 2.6,',['1080p'],'optional',fixed_resolution='1080p',audio_field='sound')
    add('market/kling/v25-turbo-'+mode+'-to-video-pro','kling/v2-5-turbo-'+mode+'-to-video-pro','Kling 2.5 Turbo Pro',[mode],[5,10],'kling 2.5 turbo,',['1080p'],'silent',fixed_resolution='1080p')
    add('market/kling/v3-turbo-'+mode+'-to-video','kling/v3-turbo-'+mode+'-to-video','Kling 3 Turbo',[mode],list(range(3,16)),'kling 3.0 turbo,',['720p','1080p'])
    add('market/kling/v3-omni-'+mode+'-to-video','kling-3.0-omni/'+mode+'-to-video','Kling 3 Omni',[mode],list(range(3,16)),'kling 3.0 omni,',['720p','1080p','4k'],'optional',audio_field='audio')
    add('market/kling/v2-1-master-'+mode+'-to-video','kling/v2-1-master-'+mode+'-to-video','Kling 2.1 Master',[mode],[5,10],'Kling 2.1,',['1080p'],'silent',fixed_resolution='1080p',price_filter='Master-')
for tier,res in [('standard','720p'),('pro','1080p')]:
    add('market/kling/v2-1-'+tier,'kling/v2-1-'+tier,'Kling 2.1 '+tier.title(),['image'],[5,10],'Kling 2.1, video-generation,',[res],'silent',fixed_resolution=res,price_filter=tier+'-')
for route in ('keyframe-to-video','reference-to-video'):
    add('market/kling/v4-'+route,'kling-4.0/'+route,'Kling 4 '+route.split('-')[0].title(),['text','image'],list(range(3,31)),'kling 4.0, '+route+',',['720p','1080p'])
for tier in ('standard','pro'):
    add('market/hailuo/2-3-image-to-video-'+tier,'hailuo/2-3-image-to-video-'+tier,'Hailuo 2.3 '+tier.title(),['image'],[6,10],'hailuo 2.3,',['768P','1080P'],'silent',price_filter=tier+'-')
    for mode in ('text','image'):
        resolutions=['1080p'] if tier=='pro' else ['768P'] if mode=='text' else ['512P','768P']
        add('market/hailuo/02-'+mode+'-to-video-'+tier,'hailuo/02-'+mode+'-to-video-'+tier,'Hailuo 02 '+tier.title(),[mode],[6] if tier=='pro' else [6,10],'hailuo 02,',resolutions,'silent',fixed_resolution=resolutions[0] if tier=='pro' or mode=='text' else None,price_filter=tier+'-')
for version in ('happyhorse','happyhorse-1-1'):
    for mode in ('text','image'):
        add('market/'+version+'/'+mode+'-to-video',version+'/'+mode+'-to-video','HappyHorse '+('1.1' if '1-1' in version else '1.0'),[mode],list(range(3,16)),'HappyHorse-'+('1.1' if '1-1' in version else '1.0')+',',['720p','1080p'])
for mode in ('text','image'):
    add('market/minimax-h3/'+mode+'-to-video','minimax-h3/'+mode+'-to-video','MiniMax H3',[mode],list(range(4,16)),'MiniMax H3, '+mode+' to video,',['768P','2K'],'included')
add('market/vidu-q4/image-to-video','vidu-q4/image-to-video','Vidu Q4',['image'],list(range(3,17)),'Vidu Q4, image-to-video,',['540p','720p','1080p','2k','4k'],'optional',audio_field='audio')
add('market/gemini-omni-video','gemini-omni-video','Gemini Omni',['text','image'],[4,6,8,10],'gemini-omni-video,',['720p','1080p','4k'],'model',price_filter='no video input')
add('market/google/gemini-omni-flash-1-1','google/gemini-omni-flash-1-1','Gemini Omni 1.1 Flash',['text','image'],[4,6,8,10],'google/gemini-omni-flash-1-1,',['360p','720p','1080p','4k'],'model',price_filter='no video input')
add('market/grok-imagine/1-5-preview','grok-imagine-video-1-5-preview','Grok Imagine 1.5 Preview',['image'],list(range(1,16)),'grok-imagine-video-1-5-preview,',['480p','720p'],'optional',audio_field='enable_audio')
add('market/kling/kling-3-0','kling-3.0/video','Kling 3',['text','image'],list(range(3,16)),'Kling 3.0, video,',['720p','1080p','4K'],'optional',audio_field='sound')
add('runway-api/generate-ai-video','runway','Runway',['text','image'],[5,10],'Runway,',['720p','1080p'])
for variant in ('Lite','Fast','Quality'):
    add('veo3-api/generate-veo-3-video','veo-3-1','Veo 3.1 '+variant,['text','image'],[4,6,8],'Google veo 3.1,',['720p','1080p','4k'],'included',price_filter=variant+'-',variant=variant.lower())

# Documented variants with insufficient duration/pricing/transport coverage remain unavailable.
excluded=[dict(model='Wan 2.2 A14B Turbo',reason='Official schema has no selectable duration; price is per second. Fixed billable output duration is not verified.'),
 dict(model='Seedance 2.5 Draft',reason='Requires a previous paid draft task; not plain text/image generation.'),
 dict(model='Grok Imagine 1.5 Preview 1080p',reason='Schema supports 1080p but the public pricing catalog has no unambiguous model-specific 1080p row.'),
 dict(model='Video editing / reference-video / avatar / lip-sync / upscale / extension models',reason='Require inputs or workflows outside text-to-video and single-image-to-video.')]
dest=Path('clipforge_studios/backend/video_catalog.json')
encoded=json.dumps(dict(updated='2026-10-26',usd_per_credit='0.005',pricing_api_supported=False,models=MODELS,excluded=excluded),indent=2)
# One auditable price row per line keeps the generated snapshot reviewable in Git.
encoded=re.sub(r'^        \{\n          "resolution":.*?^        \}',lambda match:'        '+json.dumps(json.loads(match[0]),separators=(',',':')),encoded,flags=re.S|re.M)
dest.write_text(encoded+'\n',encoding='utf-8')
print('Reviewed routes:',len(MODELS),'Priced variants:',sum(len(m['prices']) for m in MODELS))
