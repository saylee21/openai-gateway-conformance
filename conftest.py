import os

import httpx
import pytest
from openai import OpenAI

BASE_URL = os.environ.get("MOCK_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.environ.get("MOCK_API_KEY", "test")


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def client(base_url):
    return OpenAI(base_url=base_url + "/v1", api_key=API_KEY, max_retries=0, timeout=30)


@pytest.fixture(scope="session")
def http(base_url):
    with httpx.Client(
        base_url=base_url,
        timeout=30,
        headers={"Authorization": "Bearer " + API_KEY},
    ) as c:
        yield c


@pytest.fixture
def chat_body():
    def make(**overrides):
        body = {
            "model": "mock-small",
            "messages": [{"role": "user", "content": "Why is the first token slow?"}],
            "max_tokens": 8,
            "temperature": 0,
            "stream": False,
        }
        body.update(overrides)
        return body

    return make
