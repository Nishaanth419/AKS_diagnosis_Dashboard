from utils_rag import extract_llm_text

sample = {"choices":[{"text":"Hello from GPT4All"}]}
print(extract_llm_text(sample))
