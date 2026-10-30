"""One-time: the preview picture for every AI thumbnail style, all of the same fictional sample person.

Run once (it costs money: about $0.04 per style at Standard quality); the pictures are saved in the
frontend so the style gallery never generates anything again.

    python tools/make_thumbnail_style_previews.py --approve-spend --out ../review_frontend/public/studios/thumbnail-styles
"""
import argparse
import base64
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipforge_studios.backend import ai_thumbnails  # noqa: E402

PERSON = ('Photorealistic head-and-shoulders portrait of a fictional, invented person (not a real or famous individual): '
          'a friendly woman in her early thirties with shoulder-length curly auburn hair, light freckles and a warm smile, '
          'wearing a mustard-yellow knit sweater, looking at the camera, plain soft grey studio background.')
SCENE = ('The sample person enjoying a cup of coffee in a sunny neighborhood cafe, a latte with heart foam art in her hand, '
         'pastries on the counter behind her.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--approve-spend', action='store_true', help='required: this calls the paid OpenAI image API')
    parser.add_argument('--out', required=True)
    parser.add_argument('--quality', default='standard', choices=list(ai_thumbnails.QUALITIES))
    parser.add_argument('--only', nargs='*', help='style names to (re)make; default all missing')
    args = parser.parse_args()
    if not args.approve_spend:
        sys.exit('Add --approve-spend: this generates paid images.')
    from openai import OpenAI
    from PIL import Image
    client = OpenAI(timeout=300, max_retries=1)
    out = Path(args.out);out.mkdir(parents=True, exist_ok=True)
    spent = []
    person = out / 'sample-person.jpg'
    if not person.is_file():
        r = client.images.generate(model=ai_thumbnails.MODEL, prompt=PERSON, size='1024x1024', quality='medium')
        Image.open(io.BytesIO(base64.b64decode(r.data[0].b64_json))).convert('RGB').save(person, 'JPEG', quality=92)
        spent.append(ai_thumbnails.cost(r.usage) or 0)
        print('sample person', round(spent[-1], 4), flush=True)
    reference = ai_thumbnails.reference_jpeg(person)
    brief = dict(has_person=True, person='woman with curly auburn hair in a mustard sweater', scene=SCENE)
    names = args.only or [n for n in ai_thumbnails.STYLES if not (out / f'{ai_thumbnails.slug(n)}.jpg').is_file()]

    def one(name):
        try:
            png, usd = ai_thumbnails.generate(name, brief, args.quality, reference, client)
            with Image.open(io.BytesIO(png)) as image:
                image.convert('RGB').resize((640, 360), Image.LANCZOS).save(out / f'{ai_thumbnails.slug(name)}.jpg', 'JPEG', quality=86)
            return name, usd, None
        except Exception as error:
            return name, 0, str(error)[:200]
    with ThreadPoolExecutor(4) as pool:
        for name, usd, error in pool.map(one, names):
            spent.append(usd or 0)
            print(name, 'FAILED ' + error if error else round(usd or 0, 4), flush=True)
    print(json.dumps(dict(images=len(spent), total_usd=round(sum(spent), 4))))


if __name__ == '__main__':
    main()
