"""Run a Gemini request through Vertex AI using Application Default Credentials.

Run from the repository root after installing GCP dependencies and configuring
ADC (for example, with ``gcloud auth application-default login``):

    python veriproof/tests/solana/vertex_ai_gemini_test.py
"""
from __future__ import annotations


PROJECT_ID = "api-pro-178010"
LOCATION = "asia-northeast3"
MODEL_NAME = "gemini-2.5-flash"
PROMPT = "안녕하세요! ADC 연동 및 할당량 설정 후 테스트입니다."


def _load_vertex_ai():
    """Load the optional Vertex AI SDK with an actionable install error."""
    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel
    except ImportError as exc:
        raise SystemExit(
            "Vertex AI SDK is not installed. From the repository root, run: "
            "python -m pip install -r veriproof/requirements-gcp.txt"
        ) from exc
    return vertexai, GenerativeModel


def main() -> None:
    vertexai, generative_model = _load_vertex_ai()
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    print(f"Initialized Vertex AI with project {PROJECT_ID} and location {LOCATION}")
    model = generative_model(MODEL_NAME)
    print(f"Loaded model {MODEL_NAME}")
    import time
    start_time = time.time()

    response = model.generate_content(PROMPT)
    end_time = time.time()
    print(f"Time taken: {end_time - start_time} seconds")
    # print(f"Generated response: {response.text}")

    if not response.text:
        raise SystemExit("Vertex AI returned a response without text content.")
    print(response.text)


if __name__ == "__main__":
    main()
