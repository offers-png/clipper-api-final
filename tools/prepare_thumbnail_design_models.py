"""Explicit approved public downloads only: free models for designed thumbnails.

Files land in <directory>/thumbnail-design, where CLIPFORGE_MODELS_DIR=<directory>
lets clipforge_studios.backend.thumbnail_design find them. Pinned by SHA-256.
"""
import argparse
import hashlib
from pathlib import Path
import requests

FILES=[  # (url, name, sha256, license)
    ('https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx',
     'face_detection_yunet_2023mar.onnx','8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4','MIT'),
    ('https://media.githubusercontent.com/media/onnx/models/main/validated/vision/body_analysis/emotion_ferplus/model/emotion-ferplus-8.onnx',
     'emotion-ferplus-8.onnx','a2a2ba6a335a3b29c21acb6272f962bd3d47f84952aaffa03b60986e04efa61c','MIT'),
    ('https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx',
     'isnet-general-use.onnx','60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a','Apache-2.0'),
]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--approve-free-downloads',action='store_true');parser.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args()
    if not args.approve_free_downloads:parser.error('Explicit free-download approval required')
    session=requests.Session();session.trust_env=False
    folder=args.directory/'thumbnail-design';folder.mkdir(parents=True,exist_ok=True)
    for url,name,expected,license_name in FILES:
        target=folder/name
        if target.is_file() and hashlib.file_digest(target.open('rb'),'sha256').hexdigest()==expected:
            print('VERIFIED EXISTING',name,flush=True);continue
        temporary=target.with_suffix('.download');digest=hashlib.sha256()
        with session.get(url,stream=True,timeout=(15,120)) as result:
            result.raise_for_status()
            with temporary.open('wb') as output:
                for chunk in result.iter_content(1<<20):digest.update(chunk);output.write(chunk)
        if digest.hexdigest()!=expected:
            temporary.unlink();raise SystemExit(f'Checksum mismatch for {name}; nothing was kept')
        temporary.replace(target);print('DOWNLOADED',name,license_name,flush=True)

if __name__=='__main__':main()
