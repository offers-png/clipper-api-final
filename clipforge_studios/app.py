"""Isolated additive Studio API; never imports the production app or its clients."""
from .backend.server import app
from .backend import studio_ai
app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None) not in ('/','/health','/fixture','/api/videos/{video_id}/auto')]
from .backend.legacy import register as legacy
from .backend.editor import register as editor
from .backend.image_video import register as image_video
from .backend.thumbnail_ai import register as thumbnail_ai
legacy(app);editor(app);image_video(app);thumbnail_ai(app)

@app.get('/health')
def health():return dict(ok=True,ai=studio_ai.status(),mode='additive-studios',image_generation_connected=False)

from fastapi.staticfiles import StaticFiles
from .backend import media
app.mount('/media/exports',StaticFiles(directory=media.EXPORT_DIR),name='studio-exports')
app.mount('/media/thumbs',StaticFiles(directory=media.THUMB_DIR),name='studio-thumbs')
