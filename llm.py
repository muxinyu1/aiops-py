
from dataclasses import dataclass


@dataclass
class LLM():

    base_url: str
    api_key: str
    model: str

    def make_response(self, system_prompt: str, user_prompt: str):
        return NotImplemented