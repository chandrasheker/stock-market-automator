#!/bin/bash
set -e

echo "============================================"
echo " Indian Options Trading Automator - Setup"
echo "============================================"

# Create virtual environment
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "Created virtual environment"
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Download NLTK data for TextBlob
python -c "import nltk; nltk.download('punkt', quiet=True); nltk.download('brown', quiet=True)" 2>/dev/null || true

# Create directories
mkdir -p data/cache logs

# Copy env file if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "Created .env file - EDIT IT with your Zerodha API credentials"
fi

# Initialize database
python -c "from src.data.database import init_db; init_db(); print('Database initialized')"

echo ""
echo "Setup complete! Next steps:"
echo "  1. Edit .env with your Kite API key/secret"
echo "  2. Run: source venv/bin/activate"
echo "  3. Login: python -m src.main login"
echo "  4. Download data: python -m src.main download"
echo "  5. Scan: python -m src.main scan"
echo "  6. Dashboard: streamlit run src/dashboard/app.py"
echo "  7. Start bot: python -m src.main run"
echo ""
