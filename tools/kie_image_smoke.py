"""One-image KIE smoke test. Dry-run by default; never prints credentials."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clipforge_studios.backend.kie_provider import KieClient, SMOKE_MODEL

PROMPT='A simple studio photograph of a green ceramic mug on a cream background, no text.'

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--approve-paid-image-test',action='store_true')
    p.add_argument('--output',default='kie-smoke-test.png')
    args=p.parse_args(argv)
    if not args.approve_paid_image_test:
        print(json.dumps(dict(dry_run=True,model=SMOKE_MODEL,maximum_generation_submissions=1,aspect_ratio='1:1',network_requests=0,approval_required=True)))
        return 0
    path=Path(args.output)
    if not path.parent.is_dir() or any(path.with_suffix(ext).exists() for ext in ('.png','.jpg','.webp')):
        raise RuntimeError('Choose a new output path in an existing directory')
    client=KieClient(os.getenv('CLIPFORGE_VIDEO_PROVIDER_API_KEY',''))
    client.require_approval()  # Requires separate server/environment opt-in as well.
    raw=client.generate_image(PROMPT,ratio='1:1',smoke=True)
    path=path.with_suffix('.png' if raw.startswith(b'\x89PNG') else '.jpg' if raw.startswith(b'\xff\xd8') else '.webp')
    with path.open('xb') as output:output.write(raw)
    print('One image downloaded. No video or batch generation was requested.')
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print('KIE smoke test stopped. Check approval, server configuration and KIE task history before retrying.',file=sys.stderr)
        raise SystemExit(1)
