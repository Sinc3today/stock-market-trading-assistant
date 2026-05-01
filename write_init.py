import os
os.makedirs("backtests", exist_ok=True)
with open(os.path.join("backtests", "__init__.py"), "wb") as f:
    f.write(b"")
print("backtests/__init__.py written (empty, clean)")
