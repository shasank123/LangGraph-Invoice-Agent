import uvicorn
import sqlite3
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from multiprocessing import process

# --- DATABASE SETUP ---
