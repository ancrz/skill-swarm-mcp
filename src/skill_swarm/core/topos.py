"""Topos Integrity Circuit Breaker: DAG validation and anti-co-dependency guardian (SPEC-3)."""

from collections import deque
import logging

logger = logging.getLogger("skill-swarm.topos")


class ToposVerdict:
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"


class SkillToposGuard:
    """Validates skill dependency graphs to prevent circular co-dependencies."""

    @staticmethod
    def audit_skill_dependencies(
        skills: list[str],
        dependencies: dict[str, list[str]],
    ) -> tuple[str, list[str], list[str]]:
        """Audit dependency topology among skills.

        Args:
            skills: List of skill identifiers in the plan.
            dependencies: Mapping of skill -> list of dependencies.

        Returns:
            Tuple of (verdict, execution_order_or_remediation, logs).
        """
        all_nodes = set(skills)
        for deps in dependencies.values():
            all_nodes.update(deps)

        adj: dict[str, list[str]] = {n: [] for n in all_nodes}
        for node, deps in dependencies.items():
            for d in deps:
                adj[node].append(d)

        # 1. Cycle Detection via DFS
        visited: set[str] = set()
        rec_stack: set[str] = set()
        cycle_path: list[str] = []

        def dfs(curr: str, path: list[str]) -> bool:
            visited.add(curr)
            rec_stack.add(curr)
            path.append(curr)

            for neighbor in adj.get(curr, []):
                if neighbor not in visited:
                    if dfs(neighbor, path):
                        return True
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    cycle_path.extend(path[cycle_start:])
                    cycle_path.append(neighbor)
                    return True

            rec_stack.remove(curr)
            path.pop()
            return False

        for node in all_nodes:
            if node not in visited:
                if dfs(node, []):
                    cycle_repr = " -> ".join(cycle_path)
                    remediation = [
                        f"Circular co-dependency detected: [{cycle_repr}].",
                        "Topos Axiom Violation: Co-dependent modules must be decomposed via interface segregation.",
                    ]
                    return ToposVerdict.BLOCKED, [], remediation

        # 2. Topological Sort via Kahn's Algorithm
        in_degree = {n: 0 for n in all_nodes}
        dep_graph: dict[str, list[str]] = {n: [] for n in all_nodes}
        for node, deps in dependencies.items():
            for d in deps:
                dep_graph[d].append(node)
                in_degree[node] += 1

        queue = deque([n for n, deg in in_degree.items() if deg == 0])
        ordered: list[str] = []

        while queue:
            curr = queue.popleft()
            ordered.append(curr)
            for neighbor in dep_graph[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return (
            ToposVerdict.APPROVED,
            ordered,
            [f"Graph coherent: 0 circular co-dependencies detected across {len(all_nodes)} nodes."],
        )
