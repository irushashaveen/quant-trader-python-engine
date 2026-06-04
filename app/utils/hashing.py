import hashlib

def generate_signal_hash(symbol: str, direction: str, timestamp: str) -> str:
    raw = f"{symbol}:{direction}:{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()
