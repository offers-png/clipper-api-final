# models.py — pydantic models (kept small for now)
from pydantic import BaseModel, Field
from typing import List

class ClipRequest(BaseModel):
    start: str = Field(..., description="HH:MM:SS")
    end:   str = Field(..., description="HH:MM:SS")
    watermark: bool = False
    wm_text: str = "@ClipForge"
    final_1080: bool = False
    include_transcript: bool = False

class MultiClipItem(BaseModel):
    start: str
    end: str

class MultiClipRequest(BaseModel):
    sections: List[MultiClipItem]
    watermark: bool = False
    wm_text: str = "@ClipForge"
    preview_480: bool = True
    final_1080: bool = False
    include_transcript: bool = False
