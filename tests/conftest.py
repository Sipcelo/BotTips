import os
from pathlib import Path
from dotenv import load_dotenv

# Garante que o .env é carregado antes dos testes
load_dotenv(Path(__file__).parents[1] / ".env")
