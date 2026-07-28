"""Static safety checks for the declared workflow topology."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import yaml


_TERMINALS = {"done", "failed"}


def _state_machine_config() -> dict:
    repo_root = Path(__file__).resolve().parents[2]
    return yaml.safe_load((repo_root / "config/system_config/state_machine.yaml").read_text(encoding="utf-8"))


def _static_edges(config: dict) -> dict[str, set[str]]:
    states = config["states"]
    edges: dict[str, set[str]] = defaultdict(set)
    for task_id, node in states.items():
        for field in ("next_on_success", "next_on_failure"):
            target = node.get(field)
            if isinstance(target, str) and target and not target.startswith("__"):
                edges[task_id].add(target)
    return edges


def _strongly_connected_components(nodes: set[str], edges: dict[str, set[str]]) -> list[set[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlink[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in edges[node]:
            if target not in nodes:
                continue
            if target not in indices:
                visit(target)
                lowlink[node] = min(lowlink[node], lowlink[target])
            elif target in on_stack:
                lowlink[node] = min(lowlink[node], indices[target])
        if lowlink[node] == indices[node]:
            component: set[str] = set()
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node:
                    break
            components.append(component)

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return components


def test_static_state_machine_targets_are_declared_or_explicitly_dynamic() -> None:
    config = _state_machine_config()
    states = set(config["states"])
    assert config["initial_state"] in states

    unknown = sorted(
        f"{task_id}.{field} -> {target}"
        for task_id, node in config["states"].items()
        for field in ("next_on_success", "next_on_failure")
        if isinstance((target := node.get(field)), str)
        and target
        and not target.startswith("__")
        and target not in states | _TERMINALS
    )
    assert not unknown, "Undefined static state-machine targets: " + "; ".join(unknown)


def test_every_declared_static_cycle_has_an_exit() -> None:
    config = _state_machine_config()
    states = set(config["states"])
    edges = _static_edges(config)
    trapped: list[str] = []
    for component in _strongly_connected_components(states, edges):
        is_cycle = len(component) > 1 or any(node in edges[node] for node in component)
        if not is_cycle:
            continue
        exits = {target for node in component for target in edges[node] if target not in component}
        if not exits:
            trapped.append(", ".join(sorted(component)))
    assert not trapped, "Static state-machine cycles without an exit: " + "; ".join(trapped)
