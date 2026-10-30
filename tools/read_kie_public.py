"""Read public KIE documentation only; no credentials or paid endpoints."""
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import re
import json
import requests

class Text(HTMLParser):
    def __init__(self):super().__init__();self.skip=0;self.parts=[];self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.skip+=1
        if tag=='a':self.links.extend(v for k,v in attrs if k=='href' and v.startswith('/'))
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip and data.strip():self.parts.append(data.strip())

def read(url):
    if not url.startswith(('https://kie.ai/','https://docs.kie.ai/')):raise ValueError('Public documentation only')
    r=requests.get(url,timeout=45);r.raise_for_status()
    parser=Text();parser.feed(r.text)
    text='\n'.join(parser.parts) if '<html' in r.text.lower() else r.text
    folder=Path('clipforge_studios/data/pricing-research');folder.mkdir(parents=True,exist_ok=True)
    path=folder/(url.split('://')[1].replace('/','_')+'.txt');path.write_text(text,encoding='utf-8')
    if url=='https://kie.ai/pricing':
        (folder/'pricing-links.txt').write_text('\n'.join(dict.fromkeys(parser.links)),encoding='utf-8')
        (folder/'pricing.html').write_text(r.text,encoding='utf-8')
    return str(path),len(text)

if __name__=='__main__':
    urls=sys.argv[1:]
    if urls==['--catalog-schemas']:
        catalog=json.loads(Path('clipforge_studios/backend/video_catalog.json').read_text(encoding='utf-8'))
        urls=list(dict.fromkeys(m['schema_source']+'.md' for m in catalog['models']))
    if urls==['--video-schemas']:
        index=Path('clipforge_studios/data/pricing-research/docs.kie.ai_llms.txt.txt').read_text(encoding='utf-8')
        urls=[re.search(r'\((https://[^)]+)\)',line)[1] for line in index.splitlines() if line.startswith('- Video Models') and any(s in line.lower() for s in ('text-to-video','image-to-video','text to video','image to video','seedance','kling 3.0]','v2.1 pro','v2.1 standard','v3 turbo','gemini omni video','wan 3.0','kling 4.0','veo3.1 video','generate ai video'))]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(read,urls):print(*result)
