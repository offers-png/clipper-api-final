"""Explicit approved public downloads only. No credential or generation API access."""
import argparse
import hashlib
from pathlib import Path
import re
import requests

FILES=[('Qwen/Qwen3-0.6B-GGUF','Qwen3-0.6B-Q8_0.gguf','studio'),
       ('ggml-org/SmolVLM-256M-Instruct-GGUF','SmolVLM-256M-Instruct-Q8_0.gguf','vision'),
       ('ggml-org/SmolVLM-256M-Instruct-GGUF','mmproj-SmolVLM-256M-Instruct-f16.gguf','vision')]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--approve-free-downloads',action='store_true');parser.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args()
    if not args.approve_free_downloads:parser.error('Explicit free-download approval required')
    session=requests.Session();session.trust_env=False
    for repo,name,folder in FILES:
        response=session.get(f'https://huggingface.co/api/models/{repo}?blobs=true',timeout=60);response.raise_for_status()
        meta=response.json();revision=meta['sha'];assert re.fullmatch('[a-f0-9]{40}',revision)
        entry=next(s for s in meta['siblings'] if s['rfilename']==name)
        expected=entry['lfs']['sha256'];size=entry['size']
        assert re.fullmatch('[a-f0-9]{64}',expected) and 0<size<800_000_000
        target=args.directory/folder/name;target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_file() and target.stat().st_size==size and hashlib.file_digest(target.open('rb'),'sha256').hexdigest()==expected:
            print('VERIFIED EXISTING',name,flush=True);continue
        temporary=target.with_suffix('.download')
        digest=hashlib.sha256();received=0
        with session.get(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',stream=True,timeout=(15,120)) as result:
            result.raise_for_status()
            with temporary.open('wb') as output:
                for chunk in result.iter_content(1024*1024):
                    received+=len(chunk)
                    if received>size:raise ValueError('Download exceeds verified model size')
                    digest.update(chunk);output.write(chunk)
        if received!=size or digest.hexdigest()!=expected:raise ValueError('Model hash/size mismatch; not installed')
        temporary.replace(target)
        print('VERIFIED INSTALLED',name,size,revision,expected,flush=True)

if __name__=='__main__':main()
