"""Approved FREE model staging only. No public app settings or credentials change."""
import argparse
import hashlib
from pathlib import Path
import re
import requests
import shutil
import subprocess
import tarfile
import venv

FILES=[('Qwen/Qwen3-0.6B-GGUF','Qwen3-0.6B-Q8_0.gguf','studio'),
 ('ggml-org/SmolVLM-256M-Instruct-GGUF','SmolVLM-256M-Instruct-Q8_0.gguf','vision'),
 ('ggml-org/SmolVLM-256M-Instruct-GGUF','mmproj-SmolVLM-256M-Instruct-f16.gguf','vision')]
RUNTIME_URL='https://github.com/ggml-org/llama.cpp/releases/download/b11669/llama-b11669-bin-ubuntu-x64.tar.gz'
RUNTIME_SHA='cf07c1c112d709bd67e522017471de57ed0ec8bea031d732c5422351c07f65ac'

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def fetch(session,url,target,size,sha):
    if target.is_file() and target.stat().st_size==size and digest(target)==sha:
        print('VERIFIED EXISTING',target.name,flush=True);return
    target.parent.mkdir(parents=True,exist_ok=True);temporary=target.with_suffix('.download');received=0
    with session.get(url,stream=True,timeout=(15,120)) as response:
        response.raise_for_status()
        with temporary.open('wb') as output:
            for chunk in response.iter_content(1024*1024):
                received+=len(chunk)
                if received>size:raise ValueError('Oversized public model download')
                output.write(chunk)
    if received!=size or digest(temporary)!=sha:raise ValueError('Public model hash/size check failed')
    temporary.replace(target);print('VERIFIED INSTALLED',target.name,size,flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--approve-free-downloads',action='store_true');parser.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args()
    if not args.approve_free_downloads:parser.error('Explicit free-download approval required')
    root=args.directory.resolve();root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root).free<2*1024**3:raise RuntimeError('Need at least 2 GiB free disk for staging; no hosting change was attempted')
    session=requests.Session();session.trust_env=False
    files=FILES+[('Systran/faster-whisper-base.en',name,'base.en') for name in ('model.bin','config.json','tokenizer.json','vocabulary.txt')]
    metadata={}
    for repo,name,folder in files:
        if repo not in metadata:
            response=session.get(f'https://huggingface.co/api/models/{repo}?blobs=true',timeout=60);response.raise_for_status();metadata[repo]=response.json()
        info=metadata[repo];revision=info['sha'];assert re.fullmatch('[a-f0-9]{40}',revision)
        entry=next(s for s in info['siblings'] if s['rfilename']==name);size=entry['size']
        # Large binary assets have SHA-256 LFS metadata. Small JSON files are
        # pinned to the publisher commit and verified against the Git blob ID.
        if entry.get('lfs'):
            sha=entry['lfs']['sha256'];assert re.fullmatch('[a-f0-9]{64}',sha)
            fetch(session,f'https://huggingface.co/{repo}/resolve/{revision}/{name}',root/folder/name,size,sha)
        else:
            r=session.get(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',timeout=60);r.raise_for_status();raw=r.content
            assert len(raw)==size and hashlib.sha1(b'blob '+str(size).encode()+b'\0'+raw).hexdigest()==entry['blobId']
            target=root/folder/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            print('VERIFIED INSTALLED',name,size,flush=True)
    archive=root/'runtime.tar.gz';fetch(session,RUNTIME_URL,archive,17851611,RUNTIME_SHA)
    runtime=root/'runtime';runtime.mkdir(exist_ok=True)
    with tarfile.open(archive) as bundle:bundle.extractall(runtime,filter='data')
    servers=list(runtime.rglob('llama-server'));assert len(servers)==1
    servers[0].chmod(0o755)
    speech=root/'speech-env';venv.EnvBuilder(system_site_packages=True,with_pip=True).create(speech)
    python=speech/'bin/python'
    subprocess.run([str(python),'-m','pip','install','--no-cache-dir','faster-whisper==1.2.1','numpy>=2,<3'],check=True)
    print('STAGING COMPLETE',str(servers[0]),str(python),flush=True)

if __name__=='__main__':main()
