"""Read-only public pricing website catalog. NOT a generation endpoint/API contract."""
import json,requests
from pathlib import Path
url='https://api.kie.ai/client/v1/model-pricing/page'
rows=[]
for page in range(1,6):
    result=requests.post(url,json=dict(pageNum=page,pageSize=100,modelDescription='',interfaceType='Video'),timeout=45)
    result.raise_for_status();data=result.json()
    if data.get('code')!=200:raise RuntimeError('Public pricing catalog unavailable: '+str(data.get('code')))
    rows.extend(data['data']['records'])
    if page>=data['data']['pages']:break
Path('clipforge_studios/data/pricing-research/public-video-prices.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
print('Public pricing rows:',len(rows),'Total:',data['data']['total'])
for row in rows:
    print(row['modelDescription'].encode('ascii','replace').decode(), '|',row['creditPrice'],row['creditUnit'])
