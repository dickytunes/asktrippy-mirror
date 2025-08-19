import subprocess

OLLAMA_PATH = "ollama"
OLLAMA_MODEL = "mistral"

def summarize(text: str) -> str:
    """
    Uses local Mistral via Ollama to generate a concise summary of the input text.
    """
    if not text or len(text.strip()) < 20:
        return ""

    prompt = f"Summarize in 2 concise sentences:\n\n{text.strip()}"

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", OLLAMA_MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )

        output = result.stdout.strip()
        return output if output else ""

    except Exception as e:
        print(f"❌ Ollama LLM failed: {e}")
        return ""

