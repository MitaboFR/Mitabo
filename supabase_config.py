import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'mitabo.db')}"
SQLALCHEMY_TRACK_MODIFICATIONS = False

# Supabase
from supabase import create_client

SUPABASE_URL = "https://brwhbzklmkygpmpxpzbs.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJyd2hiemtsbWt5Z3BtcHhwemJzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NTUwMTY5NiwiZXhwIjoyMDcxMDc3Njk2fQ.nf0xYiie8YDMM0QEmgeLrK_QUF1o9B72d4DMNK4m9x4"  # clé anon ou service role selon besoin
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

BUCKET_NAME = "mitabo-videos"
