"""Write a human-readable report from the reviewed snapshot; no network requests."""
from pathlib import Path
import json
from decimal import Decimal
data=json.loads(Path('clipforge_studios/backend/video_catalog.json').read_text(encoding='utf-8'))
lines=['# KIE video model pricing — verified 2026-10-26','',
 'Official sources: [KIE pricing](https://kie.ai/pricing), plus each linked official model schema below. The public website catalog was checked without credentials. Its website endpoint is not a documented customer pricing API, so production uses this dated snapshot. No live-price API support is claimed.','',
 'USD estimates use exactly $0.005 per credit before top-up bonuses. Credit amounts are authoritative; marketing-page rounded USD amounts are not used. Server quotes, not client estimates, authorize spending. Quotes expire after ten minutes. Existing owner-only permissions and the $1 cumulative cap remain unchanged.','',
 f'{len(data["models"])} text/image routes, {sum(len(m["prices"]) for m in data["models"])} priced combinations. No paid generation was used to validate the catalog. Model schemas and price contracts are tested offline; actual model output quality is untested.','',
 '| Model / mode | Durations (seconds) | Resolution / audio / verified price | Schema |','|---|---|---|---|']
for m in data['models']:
 groups={}
 for p in m['prices']:
  key=(p['resolution'],p['audio']);groups.setdefault(key,[]).append(p)
 prices=[]
 for (resolution,audio),items in groups.items():
  if items[0]['source_unit']=='per second':
   rate=Decimal(items[0]['source_credits']);cost=f'{rate} credits/s (${rate*Decimal(".005"):f}/s)'
  else:
   durations={p['duration']:Decimal(p['credits']) for p in items}
   if len(set(durations.values()))==1:
    n=next(iter(durations.values()));cost=f'{n} credits/video (${n*Decimal(".005"):f})'
   else:cost='; '.join(f'{d}s: {n} credits (${n*Decimal(".005"):f})' for d,n in sorted(durations.items()))
  prices.append(f'{resolution}, {audio}: {cost}')
 ds=m['durations'];duration=f'{ds[0]}–{ds[-1]} (integer)' if ds==list(range(ds[0],ds[-1]+1)) and len(ds)>3 else ', '.join(map(str,ds))
 lines.append(f'| {m["name"]} / {m["mode"]} | {duration} | '+ '<br>'.join(prices)+f' | [Official schema]({m["schema_source"]}) |')
lines+=['','Audio: `on`/`off` are explicit provider settings; `model` means no user-selectable audio control. Metadata separately identifies included audio or silent models. Unsupported combinations (for example Runway 1080p at ten seconds) have no price row and are rejected. The single image in MiniMax H3 is inside its first-five-images-free allowance; no video/reference-audio surcharges apply because those inputs are not accepted here.','', '## Unavailable / out of scope','']
lines += ['- '+m['model']+': '+m['reason'] for m in data['excluded']]
lines += ['', 'No model with a missing price is substituted. No additional paid enhancement, extension, draft or fallback job is submitted. For Veo, enable_fallback is explicitly false. Large model costs can exceed the existing account allowance and are shown but blocked.','', '## Validation','',
 'Offline matrix test checks every price, duration, resolution, audio choice, required field, enum and supported payload type. Security tests verify cap enforcement with fractional credits, historical exposure, confirmation, altered model/audio/resolution/prompt rejection, no retries, owner authorization, and Veo high-resolution result selection. Frontend tests cover cost display and confirmation details. Production verification uses capabilities and quotes only; paid generation output remains untested.']
Path('KIE_VIDEO_MODEL_PRICING.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('Wrote KIE_VIDEO_MODEL_PRICING.md')
