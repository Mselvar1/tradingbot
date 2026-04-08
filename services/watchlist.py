class Watchlist:
    def __init__(self):
        self.tickers = ["NVDA", "BTC-USD", "XAUUSD=X"]

    def add(self, ticker: str):
        ticker = ticker.upper()
        if ticker not in self.tickers:
            self.tickers.append(ticker)
            return True
        return False

    def remove(self, ticker: str):
        ticker = ticker.upper()
        if ticker in self.tickers:
            self.tickers.remove(ticker)
            return True
        return False

    def get(self) -> list:
        return self.tickers

watchlist = Watchlist()