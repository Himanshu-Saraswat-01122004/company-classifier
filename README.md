# Company Domain Classifier 🏢→🔬

> Automatically classifies 1000+ companies from an Excel file into engineering domains (ECE / CSE / BOTH / NEITHER) using Google Gemini AI — with batch processing, multi-key rotation, and rate-limit handling built in.

---

## What Does This Do?

You give it an Excel file with a list of company names and CIN numbers.  
It tells you — for each company — whether it's relevant for:

- **CSE** (software, AI/ML, cloud, web, cybersecurity)
- **ECE** (electronics, semiconductors, embedded, VLSI, telecom)
- **BOTH** (significant hardware + software)
- **NEITHER** (finance, FMCG, retail, legal, etc.)

Plus hiring info: can freshers get hired? What roles? How confident is the AI?

**Output:** A colour-coded Excel file, one row per company, ready to share.

---

## Prerequisites

You need:
- Python 3.10 or later → [python.org/downloads](https://www.python.org/downloads/)
- A Google Gemini API key (free) → [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
- Git (optional, for cloning) → [git-scm.com](https://git-scm.com)

---

## Setup (Step by Step)

### 1. Clone or Download the Project

```bash
git clone https://github.com/Himanshu-Saraswat-01122004/company-classifier.git
cd company-classifier
```

Or just download the ZIP from GitHub and extract it.

---

### 2. Create a Virtual Environment

```bash
python3 -m venv .venv
```

Activate it:

| Platform | Command |
|----------|---------|
| Linux / Mac | `source .venv/bin/activate` |
| Windows | `.venv\Scripts\activate` |

You'll see `(.venv)` appear in your terminal — that means it's active.

---

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

### 4. Get a Gemini API Key

1. Go to [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Sign in with your Google account
3. Click **"Create API key"**
4. Copy the key (looks like `AIzaSy...`)

> 💡 **Tip:** The free tier gives you ~20 requests/minute. For faster processing, get keys from multiple Google accounts (each gets its own quota).

---

### 5. Configure Your Keys

Copy the example config file:

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in your key(s):

```env
# Single key:
GEMINI_API_KEYS=AIzaSyYOURKEYHERE

# Multiple keys (recommended — auto-rotates on rate limits):
GEMINI_API_KEYS=AIzaSyKEY1,AIzaSyKEY2,AIzaSyKEY3
```

> ⚠️ **Never share your `.env` file or commit it to Git.** It contains your private API key.

---

### 6. Prepare Your Input Excel File

Your Excel file must have these 3 columns (any row order, title rows at the top are auto-skipped):

| S.NO | Cin | Company Name |
|------|-----|--------------|
| 1 | U52190PN2021PTC200007 | INKED STORIES PRIVATE LIMITED |
| 2 | U74999DL2020PTC375182 | SHOPPER NETWORK PRIVATE LIMITED |

Set the path in `.env`:

```env
INPUT_EXCEL=/full/path/to/your/companies.xlsx
```

---

### 7. Run It

```bash
python main.py
```

That's it. You'll see live progress in the terminal:

```
2026-02-21 15:28 | INFO | Starting pipeline: 1047 companies | batch_size=20 | 53 batches
2026-02-21 15:28 | INFO | Progress: 20/1047 (1.9%) | batch 1/53 | ok=20 err=0
2026-02-21 15:29 | INFO | Progress: 40/1047 (3.8%) | batch 2/53 | ok=40 err=0
...
```

Results are saved to `classified_companies.xlsx` when done.

> 💾 **Auto-save:** Every 5 batches (~100 companies), progress is saved to `classified_companies_partial.xlsx`. If you stop early with **Ctrl+C**, this file has everything processed so far.

---

## Output File

The output Excel has one row per company with these columns:

| Column | Description |
|--------|-------------|
| S.NO | Original row number |
| CIN | Company Identification Number |
| Company Name | Company name |
| Domain | ECE / CSE / BOTH / NEITHER |
| Confidence | HIGH / MEDIUM / LOW |
| Primary Domain Area | e.g. "Cloud SaaS", "Semiconductor design" |
| Hardware or Software | Hardware / Software / Both / Neither |
| Hiring Possible | YES / NO / UNKNOWN |
| Fresher Friendly | YES / NO / UNKNOWN |
| Likely Roles | e.g. "SDE, ML Engineer, Data Analyst" |
| Reason | One-sentence explanation |
| Error | Blank = success, otherwise error details |

Rows are **colour-coded** by domain (green = CSE, blue = ECE, etc.) for quick scanning.

---

## Configuration Options

All settings go in `.env`. Here are the important ones:

```env
# === Required ===
GEMINI_API_KEYS=key1,key2,key3   # Comma-separated Gemini API keys

# === Input / Output ===
INPUT_EXCEL=companies_input.xlsx  # Path to your input file
OUTPUT_EXCEL=classified_companies.xlsx

# === Performance ===
BATCH_SIZE=20          # Companies per API call (higher = fewer calls)
MAX_WORKERS=3          # How many batches run in parallel
REQUESTS_PER_MINUTE=8  # Per-key rate limit (free tier ≈ 20 RPM)

# === Reliability ===
MAX_RETRIES=5          # Retry attempts before marking a company as failed
PARTIAL_SAVE_EVERY=5   # Save progress every N batches

# === Model ===
MODEL_NAME=gemini-2.5-flash-lite   # Which Gemini model to use
```

---

## How Key Rotation Works

With multiple API keys, the app distributes requests **round-robin** across all your keys:

```
Batch 1 → Key 1
Batch 2 → Key 2  
Batch 3 → Key 3
Batch 4 → Key 1
...
```

If a key gets a 429 (rate limited), it is **immediately switched** to the next available key — no waiting. The rate-limited key cools down in the background and rejoins the rotation automatically.

With 3 keys at 20 RPM each = **60 effective RPM** → 1047 companies finish in ~1 minute.

---

## Stopping and Resuming

- Press **Ctrl+C once** → graceful stop (saves whatever is done so far)
- Press **Ctrl+C twice** → immediate force-quit
- To resume later: results restart from scratch (the partial file shows what was done)

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: dotenv` | Make sure you activated the venv: `source .venv/bin/activate` |
| `No such file or directory` for input Excel | Set the full absolute path in `.env` → `INPUT_EXCEL=/home/user/...` |
| All 429 errors, no progress | All keys exhausted. Wait 1 minute or add more API keys |
| `Missing required columns` | Your Excel needs columns named S.NO, Cin (or CIN), Company Name |
| Output file is empty | Was the run interrupted before the first partial save? Check `classified_companies_partial.xlsx` |

---

## Project Structure

```
company-classifier/
├── main.py              # Entry point — run this
├── config.py            # All settings & configuration
├── ai_classifier.py     # Gemini API calls, batch + retry logic
├── async_pipeline.py    # Orchestrates concurrent batch processing
├── excel_handler.py     # Reads input Excel, writes output Excel
├── models.py            # Data models (CompanyRecord, ClassificationResult)
├── utils.py             # Rate limiter, API key pool, JSON helpers
├── requirements.txt     # Python dependencies
├── .env.example         # Template for your .env config
└── README.md            # This file
```

---

## License

MIT — free to use, modify, and distribute.
