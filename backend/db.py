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
notifications = _db["notifications"]   # one doc per email sent, keyed so nothing is sent twice
oauth_states = _db["oauth_states"]     # LinkedIn connect in progress: state → user + browser nonce (TTL)
run_claims = _db["run_claims"]         # one per profile per local day, so a scheduled post runs once (TTL)
li_invites = _db["li_invites"]         # links that let someone connect their LinkedIn to a profile (TTL)
usage = _db["usage"]                   # 24-hour allowances for previews, idea lists, rewrites… (TTL)
payments  = _db["payments"]
scheduled = _db["scheduled_posts"]
pending_posts = _db["pending_posts"]   # approval queue: generated posts held for the user's OK
write_history = _db["write_history"]   # what each profile made on Write (text only), kept 90 days (TTL)
daily_ideas = _db["daily_ideas"]       # each profile's ideas for one day ("<profile>:<date>"), kept 14 days (TTL)
sales_leads = _db["sales_leads"]       # Voyce team outreach tracker (/sales): one doc per lead
sales_config = _db["sales_config"]     # its shared message scripts
# Partners (partners.py, partner_panel.py): firms that offer Voyce to their clients under their own brand
partner_invites = _db["partner_invites"]         # a seat offered to one email, 14 days
partner_activity = _db["partner_activity"]       # what a partner's team sees: posted, approved, skipped, joined…
partner_seat_requests = _db["partner_seat_requests"]
partner_bills = _db["partner_bills"]             # bills sent, so the first one adds the setup fee once
partner_waitlist = _db["partner_waitlist"]       # the landing page's form: who asked to join, emailed to the partner
ai_usage = _db["ai_usage"]                       # each Claude call and drawn picture, with its account and cost
