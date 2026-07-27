from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class PatchProposal:
    path: str
    old: str = ""
    new: str = ""
    operation: Literal["modify", "create"] = "modify"
