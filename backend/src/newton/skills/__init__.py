"""Skills — reusable procedure playbooks the Conductor can follow.

Where the wiki holds *knowledge* (facts and conventions a project has settled on), a skill
holds a *procedure* — a repeatable how-to for a kind of task. Skills follow the ecosystem
`SKILL.md` convention (frontmatter `name` + `description`, then a markdown body of steps),
so community skills drop in unchanged. Storage and retrieval reuse RepoIndex, exactly like
the wiki: the most relevant skill for a task is loaded into the Plan stage's context.
"""

from .store import SkillMeta, Skills, build_skill_md, parse_frontmatter, parse_learned_skill

__all__ = ["SkillMeta", "Skills", "build_skill_md", "parse_frontmatter", "parse_learned_skill"]
