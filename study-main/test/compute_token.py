import tiktoken

text = open("hermes_prompt.txt", encoding="utf-8").read()
enc = tiktoken.encoding_for_model("gpt-4")
print(len(enc.encode(text)))