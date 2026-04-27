import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

_client = MongoClient(os.getenv("MONGO_URI"))
_db = _client["voyce"]

users     = _db["users"]
sessions  = _db["sessions"]
companies = _db["companies"]
post_log  = _db["post_log"]
li_tokens = _db["linkedin_tokens"]
