import json

file_path = "./test/memory.json"
with open(file_path, encoding="utf-8") as f:
    data = json.load(f)

print(data)

