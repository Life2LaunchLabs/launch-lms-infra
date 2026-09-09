"""Keep host/edge credentials out of application and migration containers."""
import os
from pathlib import Path
import sys
from env_file import read_env

source = Path(sys.argv[1]) if len(sys.argv)>1 else Path('.env')
target = Path(sys.argv[2]) if len(sys.argv)>2 else Path('.deploy-state/app.env')
env = read_env(source)
values = {k:v for k,v in env.items() if k.startswith(('LAUNCHLMS_', 'NEXT_PUBLIC_', 'AWS_')) or k == 'COLLAB_INTERNAL_KEY'}
for key, value in values.items():
    if any(c in value for c in "\n\r'") or '${' in value:
        raise ValueError(f'Use a literal dotenv value without single quotes/newlines for {key}')
os.umask(0o077)
target.parent.mkdir(exist_ok=True)
target.write_text(''.join(f"{k}='{v}'\n" for k,v in values.items()))
