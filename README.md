# LLM-Gateway

python -m uvicorn app.main:app --reload --port 8000

**Complete commands from scratch:**

```bash
# 1. Install Python 3.11
winget install Python.Python.3.11

# 2. Verify installation
py -3.11 --version

# 3. Navigate to backend
cd LLM-Gateway/backend

# 4. Create virtual environment
py -3.11 -m venv venv

# 5. Activate it
venv\Scripts\activate

# 6. Install dependencies
pip install -r requirements.txt

# 7. Download embedding model separately first
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# 8. Run the server
python -m uvicorn app.main:app --reload --port 8000
```

**Verify:**
- Health check: `http://localhost:8000/health`
- API docs: `http://localhost:8000/docs`

Save these commands somewhere — this is everything you need from a fresh Windows machine.