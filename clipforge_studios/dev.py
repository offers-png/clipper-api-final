"""Credential-free local API host; does not import or call production clients."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .install import install
app=FastAPI()
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://localhost:5174','http://127.0.0.1:5174'],allow_methods=['GET','POST'],allow_headers=['Content-Type'])
install(app)
