from dataclasses import dataclass

from llm import LLM
from parameter import Parameter


@dataclass
class Fuzzer():
    llm: LLM
    def fuzz(self) -> Parameter:
        return NotImplemented
