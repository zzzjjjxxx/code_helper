from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentSkill:
    name: str
    description: str
    triggers: tuple[str, ...] = ()


DEFAULT_SKILLS: tuple[AgentSkill, ...] = (
    AgentSkill(
        name='inspect',
        description='Read the relevant code, events, and memory before editing.',
        triggers=('diff', 'timeline', 'retrieval', 'memory', 'plan'),
    ),
    AgentSkill(
        name='implement',
        description='Create or modify files with the smallest safe patch.',
        triggers=('create file', 'modify file', 'fix bug', 'implement feature'),
    ),
    AgentSkill(
        name='verify',
        description='Run tests and summarize the result for handoff.',
        triggers=('tests', 'verify', 'check'),
    ),
    AgentSkill(
        name='rollback',
        description='Restore a snapshot if the change needs to be reverted.',
        triggers=('rollback', 'revert'),
    ),
)


def skill_catalog() -> list[dict[str, object]]:
    return [
        {
            'name': skill.name,
            'description': skill.description,
            'triggers': list(skill.triggers),
        }
        for skill in DEFAULT_SKILLS
    ]
