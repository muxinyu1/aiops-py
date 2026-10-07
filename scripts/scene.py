from dataclasses import dataclass


@dataclass
class Scene():
    compose_file: str
    def start(self):
        pass
    def stop(self):
        pass
    def ready(self) -> bool:
        return NotImplemented
    def restart(self):
        pass
    pass