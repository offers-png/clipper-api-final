"""Reviewed KIE schemas and dated official prices; no automatic substitutions."""
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from fastapi import HTTPException

CATALOG=json.loads(Path(__file__).with_name('video_catalog.json').read_text(encoding='utf-8'))
MODELS={m['id']:m for m in CATALOG['models']}
DEFAULT='wan/2-7-image-to-video'

def options(binding):
    mode=binding.get('mode','image')
    name=binding.get('model') or ('wan/2-7-text-to-video' if mode=='text' else DEFAULT)
    item=MODELS.get(name)
    if not item or item['mode']!=mode:raise HTTPException(422,'Choose a compatible allowed KIE model')
    resolution=binding.get('resolution') or ('720p' if '720p' in item['resolutions'] else item['resolutions'][0])
    audio=binding.get('audio') or ('off' if item['audio']=='optional' else 'model')
    return item,resolution,audio

def resolve(binding):
    item,resolution,audio=options(binding)
    if type(binding.get('duration'))!=int or not any(p['duration']==binding['duration'] and p['resolution']==resolution and p['audio']==audio for p in item['prices']):
        raise HTTPException(422,'This model has no verified price for the selected duration, resolution and audio options')
    if binding.get('ratio','16:9') not in item['ratios']:raise HTTPException(422,'This model does not support the selected aspect ratio')
    if len(binding.get('prompt',''))>item['prompt_limit']:raise HTTPException(422,f'This model allows up to {item["prompt_limit"]} prompt characters')
    return item

def estimate(binding):
    item=resolve(binding);_,resolution,audio=options(binding)
    price=next(p for p in item['prices'] if (p['duration'],p['resolution'],p['audio'])==(binding['duration'],resolution,audio))
    return dict(model=item['id'],provider_model=item['provider_model'],model_name=item['name'],duration=binding['duration'],resolution=resolution,audio=audio,
        audio_capability=item['audio'],cost_credits=float(Decimal(price['credits'])),cost_usd=format(Decimal(price['credits'])*Decimal('.005'),'.5f'),
        pricing_updated=item['pricing_updated'],pricing_source=item['pricing_source'],pricing_method=item['pricing_method'])

def catalog():
    items=[]
    for m in MODELS.values():
        item=deepcopy({k:v for k,v in m.items() if k not in ('schema_fields','schema_required','audio_field')})
        item['prices']=[{k:v for k,v in p.items() if k in ('resolution','audio','duration','credits')} for p in item['prices']]
        items.append(item)
    return items

def effective_prompt(binding):
    model=resolve(binding)
    styles={'Cinematic':'Cinematic camera movement.','Natural movement':'Subtle natural movement.',
            'Camera zoom':'Slow camera zoom.','Camera pan':'Slow camera pan.',
            'Animated character':'Animate the reference character consistently.','Custom prompt':''}
    prompt=((binding.get('prompt') or 'Bring the actual reference image to life with subtle movement.')+' '+styles.get(binding.get('motion','Cinematic'),'')).strip()
    if len(prompt)>model['prompt_limit']:raise HTTPException(422,'Shorten the prompt to leave room for the selected motion style')
    return prompt

def payload(binding,prompt,reference=None):
    """Only reviewed fields. The uploaded reference is pre-fitted to the chosen ratio."""
    model=resolve(binding);_,resolution,audio=options(binding);fields=model['schema_fields'];inputs={'prompt':prompt}
    if 'duration' in fields:inputs['duration']=str(binding['duration']) if fields['duration'].get('type')=='string' else binding['duration']
    if 'quality' in fields:inputs['quality']=resolution
    elif 'resolution' in fields:inputs['resolution']=resolution
    if binding.get('mode','image')=='text':
        if 'ratio' in fields:inputs['ratio']=binding.get('ratio','16:9')
        elif 'aspect_ratio' in fields:inputs['aspect_ratio']=binding.get('ratio','16:9')
    else:
        keys=('first_frame_url','image_url','image_urls')
        key=next((k for k in keys if k in model['schema_required']),None) or next((k for k in keys if k in fields),None)
        if not key:raise HTTPException(422,'This model has no supported single-image input')
        if not reference:raise HTTPException(422,'Reference image is required')
        inputs[key]=[reference] if key=='image_urls' else reference
        if model['provider_model'].startswith('kling-3.0-omni/'):inputs['aspect_ratio']='auto'
    if model['audio_field']:inputs[model['audio_field']]=audio=='on'
    provider=model['provider_model']
    if provider.startswith('wan/2-5-'):inputs['enable_prompt_expansion']=False
    elif provider.startswith('wan/2-7-'):inputs['prompt_extend']=False
    elif provider.startswith('grok-imagine/'):inputs['mode']='normal'
    elif provider=='kling-3.0/video':inputs.update(mode={'720p':'std','1080p':'pro','4K':'4K'}[resolution],multi_shots=False)
    elif provider=='veo-3-1':
        inputs.update(model={'lite':'veo3_lite','fast':'veo3_fast','quality':'veo3'}[model['variant']],
            generation_type='TEXT_2_VIDEO' if binding.get('mode')=='text' else 'FIRST_AND_LAST_FRAMES_2_VIDEO',enable_fallback=False)
    # Never add paid extensions, extra references, web search, draft jobs or fallback models.
    return inputs
