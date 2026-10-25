"""Conservative pre-push check. Reports paths/rule names, never matched values."""
from pathlib import Path
import re
import subprocess
import sys

repo=Path(sys.argv[1] if len(sys.argv)>1 else '.').resolve()
names=subprocess.check_output(['git','diff','--cached','--name-only','--diff-filter=ACMR'],cwd=repo).decode().splitlines()
rules={
 'private key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
 'GitHub token':r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b',
 'AWS access key':r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
 'OpenAI-style secret':r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}',
 'JWT credential':r'\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b',
}
issues=[]
for name in names:
 p=Path(name)
 if (p.name.startswith('.env') and p.name!='.env.example') or any(x in p.parts for x in ('node_modules','.venv','data','models')) or p.suffix.lower() in ('.mp4','.wav','.gguf','.onnx','.pem','.key'):
  issues.append((name,'excluded environment/runtime artifact'))
 data=subprocess.check_output(['git','show',':'+name],cwd=repo)
 try:text=data.decode('utf-8')
 except UnicodeDecodeError:issues.append((name,'unexpected binary file'));continue
 for rule,pattern in rules.items():
  if re.search(pattern,text):issues.append((name,rule))
 for match in re.finditer(r'''(?i)(?:api_key|apikey|secret|password|access_token)\s*[:=]\s*["']([^"'\r\n]{12,})["']''',text):
  if match.group(1)!='contract-test':issues.append((name,'literal credential assignment'))
for path,rule in issues:print(f'REVIEW {path}: {rule}')
if issues:sys.exit(1)
print(f'PASS: {len(names)} staged text files checked; no matching secret or excluded runtime artifact')
