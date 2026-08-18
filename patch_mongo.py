import re

with open("src/orbit/core/mongo_handler.py", "r") as f:
    content = f.read()

helper = """
    def _get_db_symbol(self, symbol: str) -> str:
        if symbol == "XAUUSDT":
            import os
            mode_env = os.getenv("ORBIT_ASSET_EXECUTION_MODES", "")
            for entry in filter(None, (item.strip() for item in mode_env.split(","))):
                try:
                    s, m = (part.strip() for part in entry.split(":", 1))
                    if s.upper() == "XAUUSDT" and m.lower() == "testnet":
                        return f"{symbol}_TESTNET"
                except ValueError:
                    continue
        return symbol
"""

content = content.replace("    def get_mongo_historical_data", helper + "\n    def get_mongo_historical_data")

# Patch get_mongo_historical_data
content = content.replace(
    'query = {\n                "symbol": symbol,',
    'query = {\n                "symbol": self._get_db_symbol(symbol),'
)

# Patch store_historical_data
content = content.replace(
    '                records.append({\n                    "symbol": symbol,',
    '                db_symbol = self._get_db_symbol(symbol)\n                records.append({\n                    "symbol": db_symbol,'
)

with open("src/orbit/core/mongo_handler.py", "w") as f:
    f.write(content)
