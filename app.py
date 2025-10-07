from fastapi import FastAPI, File, UploadFile

app = FastAPI()

@app.post("/clip")
async def clip(video_file: UploadFile = File(...)):
    return {"filename": video_file.filename}


