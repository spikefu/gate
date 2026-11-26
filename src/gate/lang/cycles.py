"""Finds groups of modules that import each other in a loop.

Why: modules in a cycle cannot be understood, tested or moved one at a time,
and which one loads first decides whether a name is defined yet. That shows up
as an import that works until an unrelated file changes the order.

Pure graph code: nodes are whatever the caller passes, here file paths.
"""

from __future__ import annotations

from collections import deque

Graph = dict[str, list[str]]


def tangles(graph: Graph) -> list[list[str]]:
    """Every group of two or more nodes that can all reach each other, each sorted, in order."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    out: list[list[str]] = []
    for root in sorted(graph):
        if root in index:
            continue
        # Tarjan's algorithm, with an explicit stack so a long import chain cannot exhaust recursion.
        work = [(root, iter(sorted(graph.get(root, ()))))]
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        while work:
            node, successors = work[-1]
            advanced = False
            for nxt in successors:
                if nxt not in index:
                    index[nxt] = low[nxt] = len(index)
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(graph.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                group = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group.append(member)
                    if member == node:
                        break
                if len(group) > 1:
                    out.append(sorted(group))
    return sorted(out)


def shortest_loop(graph: Graph, start: str) -> list[str]:
    """The shortest path from `start` back to itself, ending where it began. `start` must be in a tangle."""
    previous = {start: ""}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for nxt in sorted(graph.get(node, ())):
            if nxt == start:
                back = []
                while node:
                    back.append(node)
                    node = previous[node]
                return [*reversed(back), start]
            if nxt not in previous:
                previous[nxt] = node
                queue.append(nxt)
    return [start]
