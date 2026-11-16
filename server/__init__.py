"""

Axelered LLC
© 2026 all rights reserved

@author     Borut Svara <borut@axelered.com>
@date       2026-11-16

"""
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class RootResponse(BaseModel):
    status: str = 'ok'
    name: str = 'Axelered Papyrus'


@app.get('/')
def read_root():
    return RootResponse()
