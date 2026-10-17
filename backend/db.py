import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

_client = MongoClient(os.getenv("MONGO_URI"))
_db = _client["voyce"]

users     = _db["users"]
companies = _db["companies"]
post_log  = _db["post_log"]
li_tokens = _db["linkedin_tokens"]
waitlist  = _db["waitlist"]
voice_check_leads = _db["voice_check_leads"]   # emails left on the free Voice Check
payments  = _db["payments"]
scheduled = _db["scheduled_posts"]
pending_posts = _db["pending_posts"]   # approval queue: generated posts held for the user's OK
