"""Sparse source-layout instrumentation for material-local texture replacement."""
from __future__ import annotations

from collections import defaultdict
import re

_RUN = re.compile(r"^\s*run\s*=\s*([^;\s]+)\s*(?:;.*)?$", re.I)
_SET = re.compile(r"^(\s*)ps-t(\d+)\s*=\s*(?:(?:ref|reference|copy)\s+)?([^;\s]+)", re.I)
_ROOT = re.compile(r"^CommandListSetTexturesComponent(\d+)", re.I)
_BRANCH = re.compile(r"^(if|elif|else[ \t]+if)[ \t]+\S", re.I)


def _branch_kind(line):
    """Recognize control flow without changing the authored condition text."""
    code = line.split(';', 1)[0].strip().casefold()
    if code in ('else', 'endif'):
        return code
    match = _BRANCH.match(code)
    if match:
        return 'if' if match[1] == 'if' else 'elif'
    return None


class ComplexLayout(Exception):
    """The owner requires sparse per-slot tracking instead of one layout ID."""


def terminal_layouts(lines, bodies, root, slots, reverse, wanted, preserved_calls=()):
    """Stamp terminal layouts inside the owner, not inside shared helpers.

    Evaluate every structural branch using symbolic source identities. Runtime
    predicates remain untouched and are never re-evaluated after replacement.
    Bounded exploration falls back when a join cannot identify one final layout.
    """
    parsed = {}
    slot_index = {slot: index for index, slot in enumerate(slots)}

    def body(name):
        key = name.casefold()
        if key in parsed:
            return parsed[key]
        if key not in bodies:
            raise ComplexLayout()
        _, start, end = bodies[key]

        def sequence(index, nested=False):
            result = []
            while index < end:
                kind = _branch_kind(lines[index])
                if kind in ('endif', 'else', 'elif'):
                    if not nested:
                        raise ComplexLayout()
                    return result, index
                if kind == 'if':
                    branches = []
                    branch, stop = sequence(index + 1, True)
                    branches.append(branch)
                    while stop < end and _branch_kind(lines[stop]) == 'elif':
                        branch, stop = sequence(stop + 1, True)
                        branches.append(branch)
                    if stop < end and _branch_kind(lines[stop]) == 'else':
                        branch, stop = sequence(stop + 1, True)
                        branches.append(branch)
                    else:
                        branches.append([])
                    if stop >= end or _branch_kind(lines[stop]) != 'endif':
                        raise ComplexLayout()
                    result.append(branches)
                    index = stop + 1
                else:
                    result.append(index)
                    index += 1
            if nested:
                raise ComplexLayout()
            return result, index
        parsed[key] = sequence(start + 1)[0]
        return parsed[key]

    def execute(statements, states, stack):
        for statement in statements:
            if isinstance(statement, list):
                states = set().union(*(execute(branch, states, stack) for branch in statement))
            else:
                line = lines[statement]
                code = line.split(';', 1)[0].strip().lower()
                assignment, call = _SET.match(line), _RUN.match(line)
                if assignment and int(assignment.group(2)) in slot_index:
                    slot = slot_index[int(assignment.group(2))]
                    identity = reverse.get(assignment.group(3).casefold(), '')
                    identity = identity if identity in wanted else ''
                    next_states = set()
                    for layout, _last in states:
                        updated = list(layout)
                        updated[slot] = identity
                        next_states.add((tuple(updated), statement))
                    states = next_states
                elif call:
                    target = call.group(1).casefold()
                    if target in preserved_calls:
                        continue
                    if target in stack:
                        raise ComplexLayout()
                    next_states = set()
                    for layout, last in states:
                        results = execute(body(target), {(layout, None)}, (*stack, target))
                        next_states.update((value, statement if anchor is not None else last)
                                           for value, anchor in results)
                    states = next_states
                elif code.startswith(('return', 'while ', 'endwhile', 'post ', 'checktextureoverride',
                                      'draw', 'dispatch')):
                    raise ComplexLayout()
            if len(states) > 128:
                raise ComplexLayout()
        return states

    states = execute(body(root), {(tuple('' for _ in slots), None)}, (root.casefold(),))
    anchors = defaultdict(set)
    for layout, anchor in states:
        if anchor is not None:
            anchors[anchor].add(layout)
    if any(len(values) != 1 for values in anchors.values()):
        raise ComplexLayout()
    return {anchor: next(iter(values)) for anchor, values in anchors.items()}


class SourcePlan:
    """Only the affected owner's selectors and draw-time writes carry state."""

    def __init__(self, lines, spans, required, wanted, reverse, preserved_calls=()):
        self.preserved_calls = {name.casefold() for name in preserved_calls}
        self.before = defaultdict(list)
        self.after = defaultdict(list)
        self.declarations = []
        self.clones = []
        self.call_replacements = {}
        self.occurrences = defaultdict(lambda: defaultdict(set))
        self.layouts = {}
        self.slots = {}
        self.compact = set()
        self.reachable = defaultdict(set)
        bodies = {name.casefold(): (name, start, end) for name, start, end in spans}
        adjacency = {}
        roots_by_component, entries_by_component = defaultdict(set), defaultdict(set)
        for name, start, end in spans:
            calls = [match.group(1).casefold() for line in lines[start+1:end]
                     if (match := _RUN.match(line))]
            adjacency[name.casefold()] = set(calls)
            if not _ROOT.match(name):
                for target in calls:
                    root = _ROOT.match(target)
                    if root and int(root.group(1)) in required:
                        comp = int(root.group(1))
                        roots_by_component[comp].add(target)
                        entries_by_component[comp].add(name.casefold())

        def walk(root):
            found, pending = set(), [root]
            while pending:
                key = pending.pop()
                if key in found or key not in bodies:
                    continue
                found.add(key)
                pending.extend(adjacency[key] - found)
            return found

        def append_after(index, code):
            indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())]
            self.after[index].append(indent + code)

        for comp in sorted(required):
            # Derive ownership from calls, never from unrelated slot numbers.
            roots, entries = roots_by_component[comp], entries_by_component[comp]
            for root in sorted(roots):
                self.reachable[comp].update(walk(root))
            for key in self.reachable[comp]:
                _, start, end = bodies[key]
                for line in lines[start+1:end]:
                    match = _SET.match(line)
                    if match and (identity := reverse.get(match.group(3).casefold())) in wanted[comp]:
                        self.occurrences[comp][identity].add(int(match.group(2)))
            slots = sorted({slot for values in self.occurrences[comp].values() for slot in values})
            self.slots[comp] = slots
            if not slots:
                continue
            owner_scope = set(self.reachable[comp]) | entries
            between = []
            conditional_entries = set()
            for entry in sorted(entries):
                _, start, end = bodies[entry]
                last_draw = max((index for index in range(start+1, end)
                                 if lines[index].lstrip().lower().startswith('drawindexed')), default=start)
                active = False
                depth = 0
                for index in range(start+1, last_draw+1):
                    kind = _branch_kind(lines[index])
                    call = _RUN.match(lines[index])
                    if call and call.group(1).casefold() in roots:
                        active = True
                        if depth:
                            conditional_entries.add(entry)
                    elif active:
                        between.append(index)
                        if call:
                            owner_scope.update(walk(call.group(1).casefold()))
                    if kind == 'if':
                        depth += 1
                    elif kind == 'endif':
                        depth -= 1
            try:
                # Only commands between the selector and draws can invalidate its layout.
                intervening = set(between)
                for key in owner_scope - self.reachable[comp] - entries:
                    _, start, end = bodies[key]
                    intervening.update(range(start+1, end))
                for index in intervening:
                    line = lines[index]
                    match = _SET.match(line)
                    if match and int(match.group(2)) in slots:
                        raise ComplexLayout()
                    if line.lstrip().lower().startswith(('post ', 'checktextureoverride')):
                        raise ComplexLayout()
                    call = _RUN.match(line)
                    if (call and call.group(1).casefold() not in bodies
                            and call.group(1).casefold() not in self.preserved_calls):
                        raise ComplexLayout()
                stamps = {}
                for root in sorted(roots):
                    stamps.update(terminal_layouts(lines, bodies, root, slots, reverse,
                                                   wanted[comp], self.preserved_calls))
                values = sorted({layout for layout in stamps.values() if any(layout)})
                tokens = {layout: index+1 for index, layout in enumerate(values)}
                self.layouts[comp] = {token: layout for layout, token in tokens.items()}
                self.compact.add(comp)
                variable = f'$material_layout_c{comp}'
                self.declarations.append(f'global {variable} = 0')
                for root in sorted(roots):
                    self.after[bodies[root][1]].append(f'{variable} = 0')
                for index, layout in stamps.items():
                    append_after(index, f'{variable} = {tokens.get(layout, 0)}')
            except ComplexLayout:
                self._sparse_slots(comp, roots, entries, owner_scope, lines, bodies, reverse,
                                   wanted[comp], slots, adjacency, append_after)
            for entry in sorted(conditional_entries):
                variables = ([f'$material_layout_c{comp}'] if comp in self.compact else
                             [f'$material_source_c{comp}_t{slot}' for slot in slots])
                self.after[bodies[entry][1]].extend(f'{variable} = 0' for variable in variables)

    def _sparse_slots(self, comp, roots, entries, scope, lines, bodies, reverse,
                      wanted, slots, adjacency, append_after):
        tokens = {identity: index+1 for index, identity in enumerate(sorted(wanted))}
        self.layouts[comp] = tokens
        variables = {slot: f'$material_source_c{comp}_t{slot}' for slot in slots}
        self.declarations.extend(f'global {variable} = 0' for variable in variables.values())
        reset = [f'{variable} = 0' for variable in variables.values()]
        for root in sorted(roots):
            self.after[bodies[root][1]].extend(reset)
        # Keep shared helpers byte-identical for callers outside this owner.
        changed = set()
        for key in scope:
            _, start, end = bodies[key]
            if any((m := _SET.match(line)) and int(m.group(2)) in slots
                   or (r := _RUN.match(line)) and r.group(1).casefold() not in bodies
                   and r.group(1).casefold() not in self.preserved_calls
                   for line in lines[start+1:end]):
                changed.add(key)
        while True:
            parents = {key for key in scope if adjacency[key] & changed}
            if parents.issubset(changed):
                break
            changed.update(parents)
        helpers = changed - roots - entries
        renames = {key: f'CommandListMaterialScopeC{comp}_{index}'
                   for index, key in enumerate(sorted(helpers))}
        for key in sorted(changed):
            _, start, end = bodies[key]
            clone = [f'[{renames[key]}]'] if key in renames else None
            for index in range(start+1, end):
                line = lines[index]
                indent = line[:len(line)-len(line.lstrip())]
                additions = []
                assignment, call = _SET.match(line), _RUN.match(line)
                if assignment and int(assignment.group(2)) in slots:
                    slot = int(assignment.group(2))
                    identity = reverse.get(assignment.group(3).casefold()) if key in self.reachable[comp] else None
                    additions.append(f'{variables[slot]} = {tokens.get(identity, 0)}')
                if call:
                    target = call.group(1).casefold()
                    if target in renames:
                        line = line.replace(call.group(1), renames[target], 1)
                    elif target not in bodies and target not in self.preserved_calls:
                        additions.extend(reset)
                if clone is not None:
                    clone.append(line)
                    clone.extend(indent + addition for addition in additions)
                else:
                    if line != lines[index]:
                        self.call_replacements[index] = line
                    for addition in additions:
                        append_after(index, addition)
            if clone is not None:
                self.clones.extend(['', *clone])

    def conditions(self, comp, replacements):
        """Group simultaneous slot assignments under the smallest selector test."""
        if comp in self.compact:
            target_by_identity = dict(replacements)
            buckets = defaultdict(list)
            for token, layout in self.layouts[comp].items():
                assignments = tuple((slot, target_by_identity[identity])
                                    for slot, identity in zip(self.slots[comp], layout)
                                    if identity in target_by_identity)
                if assignments:
                    buckets[assignments].append(token)
            return [(" || ".join(f'$material_layout_c{comp} == {token}' for token in tokens), assignments)
                    for assignments, tokens in buckets.items()]
        return [(f'$material_source_c{comp}_t{slot} == {self.layouts[comp][identity]}', ((slot, target),))
                for identity, target in replacements for slot in sorted(self.occurrences[comp].get(identity, ()))]
