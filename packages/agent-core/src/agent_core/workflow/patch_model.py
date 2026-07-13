from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PatchProposal:
    path: str
    old: str
    new: str
