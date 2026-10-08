# Automated Evidence Scraper

An advanced, stealth web scraping and automation framework powered by **rebrowser-playwright**, automated captcha bypass (reCAPTCHA audio solver & Cloudflare Turnstile handling), and multi-engine evidence collection pipelines.

---

## 🚀 Key Features

- **Stealth & Anti-Bot Evasion**:
  - Built on `rebrowser-playwright` with custom stealth initialization scripts (`Runtime.enable` leak patch).
  - Browser fingerprint masking and randomized user-agent/viewport simulation.
- **Automated Captcha Solving**:
  - Audio-based reCAPTCHA v2 solver utilizing FFmpeg audio conversion and SpeechRecognition.
  - Automated Cloudflare Turnstile detection and interaction routines.
- **Evidence & Brand Scraping**:
  - Multi-target search and evidence harvesting.
  - AMP and brand pipeline verification.
  - Structured extraction to JSON and text summaries.

---

## 🛠️ Project Structure

```text
├── Final.py                     # Main unified stealth scraping & captcha pipeline
├── RecaptchaSolver_new.py       # Audio-based reCAPTCHA solver module
├── amp_54_brand_pipeline.py     # AMP brand verification pipeline
├── scrape_links.py              # Targeted link harvester
├── search_and_scrape.py         # Search engine query & scraping script
├── evidence-scrap.mjs           # Node.js Playwright evidence collection runner
├── scripts/                     # Reusable stealth scripts and templates
├── mathi/                       # Experimental & supporting scraping modules
├── requirements.txt             # Python dependencies
├── package.json                 # Node.js dependencies
└── instruction.txt              # Quick execution instructions
```

---

## 📦 Prerequisites & Installation

### 1. Prerequisites
- **Python 3.10+**
- **Node.js 18+**
- **FFmpeg & FFprobe**: Required for audio captcha solving. Ensure they are placed in `bin/` or installed in your system `PATH`.

### 2. Install Node Dependencies
```bash
npm install
```

### 3. Install Python Dependencies
```bash
pip install -r requirements.txt
```

---

## 🚦 Usage

### Run the Evidence Collection Script (Node.js)
```bash
node evidence-scrap.mjs
# or
npm start
```

### Run the Main Pipeline (Python)
```bash
python Final.py
```

### Run Search & Scrape
```bash
python search_and_scrape.py
```

---

## 📄 License
MIT License
