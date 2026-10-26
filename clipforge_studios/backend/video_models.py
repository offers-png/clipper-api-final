"""Server-only allowlist. Official KIE schemas/prices verified 2026-10-26.

kie.ai/wan-2-5 (12 credits/s at 720p), kie.ai/wan-2-7-video (16/s).
docs.kie.ai/market/wan/{2-5,2-7}-{text,image}-to-video
"""
from fastapi import HTTPException

DEFAULT='wan/2-7-image-to-video'
MODELS={f'wan/{version}-{mode}-to-video':dict(
    id=f'wan/{version}-{mode}-to-video',name=f'Wan {version.replace("-",".")} (720p)',
    mode=mode,durations=[5,10],ratios=['16:9','9:16'],credits_per_second=rate,
    prompt_limit=800 if version=='2-5' else 2000,
    api_durations=[5,10] if version=='2-5' else list(range(2,16)))
    for version,rate in [('2-5',12),('2-7',16)] for mode in ('text','image')}

def resolve(binding):
    mode=binding.get('mode','image')
    name=binding.get('model') or ('wan/2-7-text-to-video' if mode=='text' else DEFAULT)
    item=MODELS.get(name)
    if not item or item['mode']!=mode:raise HTTPException(422,'Choose a compatible allowed KIE model')
    duration=binding.get('duration')
    if type(duration)!=int or duration not in item['api_durations']:raise HTTPException(422,'This model does not support the selected duration')
    if binding.get('ratio','16:9') not in ('16:9','9:16','1:1'):raise HTTPException(422,'Unsupported aspect ratio')
    if len(binding.get('prompt',''))>item['prompt_limit']:raise HTTPException(422,f'This model allows up to {item["prompt_limit"]} prompt characters')
    return item

def catalog():return [dict({k:v for k,v in m.items() if k!='api_durations'},durations=m['api_durations'],ratios=['16:9','9:16','1:1']) for m in MODELS.values()]

def effective_prompt(binding):
    model=resolve(binding)
    styles={'Cinematic':'Cinematic camera movement.','Natural movement':'Subtle natural movement.',
            'Camera zoom':'Slow camera zoom.','Camera pan':'Slow camera pan.',
            'Animated character':'Animate the reference character consistently.','Custom prompt':''}
    prompt=((binding.get('prompt') or 'Bring the actual reference image to life with subtle movement.')+' '+styles.get(binding.get('motion','Cinematic'),'')).strip()
    if len(prompt)>model['prompt_limit']:raise HTTPException(422,'Shorten the prompt to leave room for the selected motion style')
    return prompt
