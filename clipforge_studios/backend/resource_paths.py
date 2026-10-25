"""Explicit local resources. Never downloads models or reads production secrets."""
import os
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
MODELS=Path((os.environ.get('CLIPFORGE_MODELS_DIR') or str(BASE/'models'))).resolve()
DATA=Path((os.environ.get('CLIPFORGE_STUDIO_DATA_DIR') or str(BASE/'data'))).resolve()
DATA.mkdir(parents=True,exist_ok=True)
